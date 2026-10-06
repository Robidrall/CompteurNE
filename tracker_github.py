import csv
import json
import os
import sys
import requests
import pandas as pd # Importé ici pour éviter les bugs sur GitHub Actions
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

# Import des fonctions depuis tes autres fichiers
from extraction import recuperer_donnees
from visualisation import generer_graphique_departements

# --- Paramètres ---
PAS_PALIER = 50    # Alerte tweet pour les franchissements (ex: 39300, 39350)
SEUIL_HEURES = 6   # Alerte e-mail point d'étape toutes les 6 heures max
OBJECTIF_FINAL = 100000

CSV_FILE = "data/adherents.csv"
CSV_FILE_DEP = "data/departements.csv"
STATE_FILE = "data/last_alert.json"
IMAGE_FILE = "data/top15_departements.png"

SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")
DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", SMTP_USER)

MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", 
           "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

def sauvegarder_csv_national(iso_date, adherents):
    """Sauvegarde le total national."""
    os.makedirs("data", exist_ok=True)
    existe = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["timestamp", "adherents"])
        writer.writerow([iso_date, adherents])

def sauvegarder_csv_departements(iso_date, departements):
    """Sauvegarde le détail de chaque département."""
    os.makedirs("data", exist_ok=True)
    existe = os.path.isfile(CSV_FILE_DEP)
    with open(CSV_FILE_DEP, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["timestamp", "code", "nom", "adherents", "parrainages", "rang"])
        for dep in departements:
            if dep.get('code'):
                writer.writerow([
                    iso_date, 
                    dep.get('code'), 
                    dep.get('nom'), 
                    dep.get('adherents', 0),
                    dep.get('parrainages', 0),
                    dep.get('rang', '')
                ])

def calculer_gain_historique(maintenant, adherents_actuels, jours_arriere):
    """Cherche dans le CSV le gain par rapport à X jours en arrière."""
    if not os.path.isfile(CSV_FILE):
        return 0
        
    cible = maintenant - timedelta(days=jours_arriere)
    adherents_historique = None
    
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        lignes = list(csv.reader(f))
        if len(lignes) <= 1:
            return 0
            
        # Parcours inversé pour trouver la ligne la plus proche de la cible
        for row in reversed(lignes[1:]):
            try:
                dt = datetime.fromisoformat(row[0])
                
                # Ajout de l'information de fuseau horaire si elle est manquante
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                    
                if dt <= cible:
                    adherents_historique = int(row[1])
                    break
            except ValueError:
                continue
                
        # Si on ne remonte pas assez loin, on prend la toute première donnée disponible
        if adherents_historique is None:
            adherents_historique = int(lignes[1][1])
            
    return adherents_actuels - adherents_historique

def lire_etat():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def ecrire_etat(iso_date, adherents, dernier_palier):
    os.makedirs("data", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": iso_date,
            "adherents": adherents,
            "dernier_palier": dernier_palier
        }, f, indent=2)

def envoyer_email_avec_piece_jointe(sujet, contenu, chemin_image=None):
    """Envoie un e-mail avec texte et image attachée."""
    msg = MIMEMultipart()
    msg["Subject"] = sujet
    msg["From"] = SMTP_USER
    msg["To"] = DESTINATAIRE

    # Attacher le texte
    msg.attach(MIMEText(contenu, "plain", "utf-8"))

    # Attacher l'image graphique si elle existe
    if chemin_image and os.path.isfile(chemin_image):
        with open(chemin_image, "rb") as f:
            img_data = f.read()
        image = MIMEImage(img_data, name=os.path.basename(chemin_image))
        msg.attach(image)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
    print(f"📧 E-mail envoyé avec succès (avec graphique) à {DESTINATAIRE}", flush=True)

def run():
    # Vérification de l'environnement
    if not all([SMTP_USER, SMTP_PASS]):
        print("Erreur : identifiants GMAIL manquants.", flush=True)
        sys.exit(1)

    # 1. Extraction (Appelle extraction.py)
    adherents, departements = recuperer_donnees()
    if adherents is None or not departements:
        print("Erreur : Impossible d'extraire les données.", flush=True)
        sys.exit(1)

    maintenant = datetime.now(timezone.utc)
    maintenant_iso = maintenant.isoformat()

    # 2. Archivage
    sauvegarder_csv_national(maintenant_iso, adherents)
    sauvegarder_csv_departements(maintenant_iso, departements)
    
    # 3. Calcul des KPIs historiques (24h et 7 jours)
    gain_24h = calculer_gain_historique(maintenant, adherents, 1)
    gain_7j = calculer_gain_historique(maintenant, adherents, 7)
    moyenne_jour = gain_7j / 7 if gain_7j > 0 else 0
    
    print(f"Relevé archivé : {adherents} adhérents (+{gain_24h} en 24h, +{gain_7j} sur 7j)", flush=True)

    palier_actuel = (adherents // PAS_PALIER) * PAS_PALIER
    etat = lire_etat()

    if etat is None:
        ecrire_etat(maintenant_iso, adherents, palier_actuel)
        print(f"État initialisé à {adherents} (palier de départ : {palier_actuel}).", flush=True)
        return

    dernier_total = etat.get("adherents", adherents)
    derniere_date = datetime.fromisoformat(etat["timestamp"])
    dernier_palier = etat.get("dernier_palier", (dernier_total // PAS_PALIER) * PAS_PALIER)

    delta_heures = (maintenant - derniere_date).total_seconds() / 3600

    doit_notifier = False
    type_alerte = ""

    # 4. Logique de déclenchement (Palier ou Temps)
    if palier_actuel > dernier_palier:
        doit_notifier = True
        type_alerte = f"⚡ Cap des {palier_actuel:,} adhérents franchi !".replace(",", " ")
        dernier_palier = palier_actuel
    elif delta_heures >= SEUIL_HEURES:
        doit_notifier = True
        type_alerte = f"📊 Point d'étape (6h) : {adherents:,} adhérents".replace(",", " ")

    # 5. Création et Envoi de l'alerte
    if doit_notifier:
        # Gamification et prévisions
        pourcentage_final = (adherents / OBJECTIF_FINAL) * 100
        prochain_gros_cap = ((adherents // 10000) + 1) * 10000
        reste_avant_cap = prochain_gros_cap - adherents
        
        if moyenne_jour > 0:
            jours_restants = reste_avant_cap / moyenne_jour
            eta_date = maintenant + timedelta(days=jours_restants)
            eta_texte = f"{int(eta_date.day)} {MOIS_FR[eta_date.month]} {eta_date.year}"
        else:
            eta_texte = "Non estimable"

        tweet_texte = (
            f"Point d'étape · @nouv_energie\n\n"
            f"👥 {adherents:,} adhérents\n"
            f"🎯 {pourcentage_final:.1f} % de l'objectif final ({OBJECTIF_FINAL:,})\n"
            f"🏁 Plus que {reste_avant_cap:,} avant le cap des {prochain_gros_cap // 1000}k !\n\n"
            f"📈 +{gain_24h:,} sur 24h\n"
            f"📈 +{gain_7j:,} sur 7 jours (~{int(moyenne_jour)}/j)\n"
            f"🗓️ Cap {prochain_gros_cap // 1000}k estimé le : {eta_texte}\n\n"
            f"#NouvelleEnergie #DavidLisnard"
        ).replace(",", " ")

        email_corps = (
            f"Bonjour Romain,\n\n"
            f"Un nouveau point d'étape a été déclenché par le tracker.\n\n"
            f"--- TEXTE À COPIER / COLLER POUR LE TWEET ---\n\n"
            f"{tweet_texte}\n\n"
            f"----------------------------------------------\n"
            f"Lien direct texte : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_texte)}\n\n"
            f"⚠️ Pense à télécharger la pièce jointe pour l'ajouter à ton Tweet !"
        )

        # Génération de l'image (Appelle visualisation.py)
        print("🎨 Génération du graphique des départements...")
        try:
            generer_graphique_departements(departements, IMAGE_FILE)
            image_a_joindre = IMAGE_FILE
        except Exception as e:
            print(f"Erreur lors de la génération du graphique : {e}")
            image_a_joindre = None

        # Envoi effectif
        envoyer_email_avec_piece_jointe(type_alerte, email_corps, image_a_joindre)
        ecrire_etat(maintenant_iso, adherents, dernier_palier)
        
    else:
        print(f"Pas d'alerte à déclencher. Prochain palier : {palier_actuel + PAS_PALIER}.", flush=True)

if __name__ == "__main__":
    run()