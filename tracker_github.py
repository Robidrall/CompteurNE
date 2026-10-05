import csv
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
import json
import os
import re
import smtplib
import sys
import requests

# --- Paramètres ---
PAS_PALIER = 50  # Alertes tous les 100 (38000, 38100, 38200...)
SEUIL_HEURES = 6   # Alerte toutes les 6 heures max
OBJECTIF_FINAL = 100000

URL = "https://adherent.unenouvelleenergie.fr/parrainer"
CSV_FILE = "data/adherents.csv"
STATE_FILE = "data/last_alert.json"

COOKIE = os.getenv("EA_SESSION")
SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")
DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", SMTP_USER)

MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", 
           "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

def extraire_donnees():
    session = requests.Session()
    session.cookies.set("ea_session", COOKIE, domain="adherent.unenouvelleenergie.fr")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = session.get(URL, headers=headers, timeout=15)
    resp.raise_for_status()

    match_collectif = re.search(r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text)
    match_gain = re.search(r'\\?"gainSemaine\\?"\s*:\s*(\d+)', resp.text)

    if match_collectif:
        total = int(match_collectif.group(1))
        gain_7j = int(match_gain.group(1)) if match_gain else 0
        return total, gain_7j
    return None, None


def sauvegarder_csv(iso_date, adherents, gain_7j):
    os.makedirs("data", exist_ok=True)
    existe = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["timestamp", "adherents", "gain_7j"])
        writer.writerow([iso_date, adherents, gain_7j])


def calculer_gain_24h(maintenant, adherents_actuels):
    """Cherche dans le CSV le nombre d'adhérents il y a 24h."""
    if not os.path.isfile(CSV_FILE):
        return 0
        
    cible = maintenant - timedelta(hours=24)
    adherents_24h = None
    
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        lignes = list(csv.reader(f))
        if len(lignes) <= 1:
            return 0
            
        for row in reversed(lignes[1:]):
            try:
                dt = datetime.fromisoformat(row[0])
                if dt <= cible:
                    adherents_24h = int(row[1])
                    break
            except ValueError:
                continue
                
        if adherents_24h is None:
            adherents_24h = int(lignes[1][1])
            
    return adherents_actuels - adherents_24h


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


def envoyer_email(sujet, contenu):
    msg = MIMEText(contenu, "plain", "utf-8")
    msg["Subject"] = sujet
    msg["From"] = SMTP_USER
    msg["To"] = DESTINATAIRE

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
    print(f"E-mail envoyé avec succès à {DESTINATAIRE}", flush=True)


def run():
    if not all([COOKIE, SMTP_USER, SMTP_PASS]):
        print("Erreur : variables d'environnement manquantes.", flush=True)
        sys.exit(1)

    adherents, gain_7j = extraire_donnees()
    if adherents is None:
        print("Impossible d'extraire les données.", flush=True)
        sys.exit(1)

    maintenant = datetime.now(timezone.utc)
    maintenant_iso = maintenant.isoformat()

    # Archivage et KPIs temporels
    sauvegarder_csv(maintenant_iso, adherents, gain_7j)
    gain_24h = calculer_gain_24h(maintenant, adherents)
    moyenne_jour = gain_7j / 7 if gain_7j > 0 else 0
    
    print(f"Relevé archivé : {adherents} adhérents (+{gain_24h} en 24h, +{gain_7j} sur 7j)", flush=True)

    palier_actuel = (adherents // PAS_PALIER) * PAS_PALIER
    etat = lire_etat()

    if etat is None:
        ecrire_etat(maintenant_iso, adherents, palier_actuel)
        print(f"État initialisé à {adherents} (palier rond de départ : {palier_actuel}).", flush=True)
        return

    dernier_total = etat.get("adherents", adherents)
    derniere_date = datetime.fromisoformat(etat["timestamp"])
    dernier_palier = etat.get("dernier_palier", (dernier_total // PAS_PALIER) * PAS_PALIER)

    delta_heures = (maintenant - derniere_date).total_seconds() / 3600

    doit_notifier = False
    type_alerte = ""

    if palier_actuel > dernier_palier:
        doit_notifier = True
        type_alerte = f"⚡ Cap des {palier_actuel:,} adhérents franchi !".replace(",", " ")
        dernier_palier = palier_actuel
    elif delta_heures >= SEUIL_HEURES:
        doit_notifier = True
        type_alerte = f"📊 Point d'étape (6h) : {adherents:,} adhérents".replace(",", " ")

    if doit_notifier:
        # Calculs Gamification et Prédictifs
        pourcentage_final = (adherents / OBJECTIF_FINAL) * 100
        prochain_gros_cap = ((adherents // 10000) + 1) * 10000
        reste_avant_cap = prochain_gros_cap - adherents
        
        # Projection (ETA)
        if moyenne_jour > 0:
            jours_restants = reste_avant_cap / moyenne_jour
            eta_date = maintenant + timedelta(days=jours_restants)
            eta_texte = f"{int(eta_date.day)} {MOIS_FR[eta_date.month]} {eta_date.year}"
        else:
            eta_texte = "Non estimable (croissance nulle)"

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
            f"Bonjour,\n\n"
            f"Un nouveau point d'étape est disponible.\n\n"
            f"--- TEXTE À COPIER / COLLER POUR LE TWEET ---\n\n"
            f"{tweet_texte}\n\n"
            f"----------------------------------------------\n"
            f"Lien direct pour tweeter : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_texte)}"
        )

        envoyer_email(type_alerte, email_corps)
        ecrire_etat(maintenant_iso, adherents, dernier_palier)
    else:
        print(f"Pas d'alerte. Prochain palier : {palier_actuel + PAS_PALIER}.", flush=True)


if __name__ == "__main__":
    run()
