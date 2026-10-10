import csv
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import json
import os
import re
import smtplib
import sys
import requests

import matplotlib
matplotlib.use("Agg")  # Mode headless sans interface graphique
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- Paramètres généraux ---
PAS_PALIER = 50        # Alerte tous les 50 nouveaux adhérents
PAS_CAP = 5000         # Grands caps (35k, 40k, 45k...)
SEUIL_HEURES = 6       # Notification récurrente au bout de 6h sans nouveau palier
TAILLE_BARRE = 10      # Longueur de la barre de progression textuelle

URL = "https://adherent.unenouvelleenergie.fr/parrainer"
CSV_FILE = "data/adherents.csv"
STATE_FILE = "data/last_alert.json"
CHART_FILE = "data/stats_card.png"

COOKIE = os.getenv("EA_SESSION")
SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")
DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", SMTP_USER)

MOIS_FR = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", 
           "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def formater_nombre(n: int) -> str:
    """Formate les entiers avec des espaces insécables."""
    return f"{n:,}".replace(",", " ")


def generer_jauge_texte(actuel: int, palier_bas: int, palier_haut: int, taille: int = 10) -> str:
    progression = (actuel - palier_bas) / (palier_haut - palier_bas)
    progression = max(0.0, min(1.0, progression))
    pleins = int(round(progression * taille))
    vides = taille - pleins
    pct = int(progression * 100)
    return f"{'▓' * pleins}{'░' * vides} {pct} %"


def calculer_velocite(gain_24h: int, gain_7j: int):
    """Calcule la cadence moyenne et le momentum vs 7 jours."""
    moyenne_jour_7j = gain_7j / 7 if gain_7j > 0 else 0

    # 1. Cadence (adhésions / heure ou minutes / adhésion)
    if gain_24h > 0:
        minutes_par_adhesion = (24 * 60) / gain_24h
        if minutes_par_adhesion >= 60:
            cadence_texte = f"~{(gain_24h / 24):.1f} adh. / heure"
        else:
            cadence_texte = f"1 adhésion toutes les {int(minutes_par_adhesion)} min"
    else:
        cadence_texte = "Rythme temporairement calme"

    # 2. Momentum (% d'écart vs moyenne hebdo)
    if moyenne_jour_7j > 0:
        ecart_pct = ((gain_24h - moyenne_jour_7j) / moyenne_jour_7j) * 100
        if ecart_pct >= 10:
            momentum_texte = f"🟢 En accélération (+{int(ecart_pct)} % vs moy. 7j)"
        elif ecart_pct <= -10:
            momentum_texte = f"🟠 En décélération ({int(ecart_pct)} % vs moy. 7j)"
        else:
            momentum_texte = "⚪ Rythme stable (conforme à la moy. 7j)"
    else:
        momentum_texte = "⚪ Données d'accélération en cours de calcul"

    return cadence_texte, momentum_texte, moyenne_jour_7j


def generer_carte_visuelle(adherents: int, gain_24h: int, gain_7j: int, 
                           cap_precedent: int, prochain_cap: int, output_path: str):
    """Génère une carte statistique 1200x675 px sombre prête pour Twitter/X."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Récupération de l'historique récent pour le mini-graphique
    dates_hist, values_hist = [], []
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
            for row in reader[1:]:
                try:
                    dates_hist.append(datetime.fromisoformat(row[0]))
                    values_hist.append(int(row[1]))
                except (ValueError, IndexError):
                    continue

    # Setup de la figure (Format 16:9 Twitter Card)
    fig = plt.figure(figsize=(12, 6.75), dpi=100, facecolor="#0B132B")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#0B132B")

    # Trace de la courbe d'évolution en fond (dernier tiers bas)
    if len(dates_hist) >= 2:
        # On garde les 14 derniers jours s'il y a du recul
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        filtered = [(d, v) for d, v in zip(dates_hist, values_hist) if d >= cutoff]
        if len(filtered) >= 2:
            d_plot, v_plot = zip(*filtered)
        else:
            d_plot, v_plot = dates_hist, values_hist

        ax_curve = fig.add_axes([0.08, 0.12, 0.84, 0.32], facecolor="none")
        ax_curve.plot(d_plot, v_plot, color="#38BDF8", linewidth=3.5)
        ax_curve.fill_between(d_plot, v_plot, min(v_plot) - 50, color="#38BDF8", alpha=0.15)
        ax_curve.tick_params(colors="#94A3B8", labelsize=10)
        ax_curve.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax_curve.spines["top"].set_visible(False)
        ax_curve.spines["right"].set_visible(False)
        ax_curve.spines["left"].set_visible(False)
        ax_curve.spines["bottom"].set_color("#334155")
        ax_curve.grid(axis="y", color="#334155", linestyle="--", alpha=0.4)

    # Textes & KPIs
    pct_prog = int(((adherents - cap_precedent) / (prochain_cap - cap_precedent)) * 100)
    pct_prog = max(0, min(100, pct_prog))

    fig.text(0.08, 0.88, "NOUVELLE ÉNERGIE · SUIVI DES ADHÉSIONS", 
             color="#94A3B8", fontsize=15, weight="bold")
    fig.text(0.08, 0.73, formater_nombre(adherents), 
             color="#FFFFFF", fontsize=50, weight="heavy")
    fig.text(0.48, 0.74, "adhérents", 
             color="#38BDF8", fontsize=24, weight="bold")

    # Badges latéraux
    fig.text(0.68, 0.84, f"+{formater_nombre(gain_24h)} en 24h", 
             color="#34D399", fontsize=18, weight="bold")
    fig.text(0.68, 0.77, f"+{formater_nombre(gain_7j)} sur 7j", 
             color="#38BDF8", fontsize=18, weight="bold")

    # Barre de progression graphique du cap
    fig.text(0.08, 0.58, f"Progression vers le cap des {prochain_cap // 1000}k : {pct_prog} % (reste {formater_nombre(prochain_cap - adherents)})", 
             color="#CBD5E1", fontsize=14, weight="medium")

    # Rectangle jauge
    rect_bg = plt.Rectangle((0.08, 0.51), 0.84, 0.035, transform=fig.transFigure,
                            facecolor="#1E293B", edgecolor="none", clip_on=False)
    fig.patches.append(rect_bg)
    rect_fg = plt.Rectangle((0.08, 0.51), 0.84 * (pct_prog / 100), 0.035, transform=fig.transFigure,
                            facecolor="#38BDF8", edgecolor="none", clip_on=False)
    fig.patches.append(rect_fg)

    ax.axis("off")
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


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


def envoyer_email(sujet, contenu, image_path=None):
    msg = MIMEMultipart()
    msg["Subject"] = sujet
    msg["From"] = SMTP_USER
    msg["To"] = DESTINATAIRE

    msg.attach(MIMEText(contenu, "plain", "utf-8"))

    if image_path and os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(image_path)}"')
            msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
    print(f"E-mail (avec visuel) envoyé avec succès à {DESTINATAIRE}", flush=True)


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

    sauvegarder_csv(maintenant_iso, adherents, gain_7j)
    gain_24h = calculer_gain_24h(maintenant, adherents)

    palier_actuel = (adherents // PAS_PALIER) * PAS_PALIER
    etat = lire_etat()

    if etat is None:
        ecrire_etat(maintenant_iso, adherents, palier_actuel)
        print(f"État initialisé à {adherents} (palier : {palier_actuel}).", flush=True)
        return

    dernier_total = etat.get("adherents", adherents)
    derniere_date = datetime.fromisoformat(etat["timestamp"])
    dernier_palier = etat.get("dernier_palier", (dernier_total // PAS_PALIER) * PAS_PALIER)

    delta_heures = (maintenant - derniere_date).total_seconds() / 3600
    doit_notifier = False
    titre_sujet = ""

    # Détection des événements
    franchissement_5k = (adherents // PAS_CAP) > (dernier_total // PAS_CAP)
    palier_5k_franchi = (adherents // PAS_CAP) * PAS_CAP

    if franchissement_5k:
        doit_notifier = True
        titre_sujet = f"🚀 CAP DES {formater_nombre(palier_5k_franchi)} ADHÉRENTS FRANCHI !"
        dernier_palier = palier_actuel
    elif palier_actuel > dernier_palier:
        doit_notifier = True
        titre_sujet = f"⚡ +{PAS_PALIER} adhérents ({formater_nombre(adherents)})"
        dernier_palier = palier_actuel
    elif delta_heures >= SEUIL_HEURES:
        doit_notifier = True
        titre_sujet = f"📊 Baromètre 6h : {formater_nombre(adherents)} adhérents"

    if doit_notifier:
        cap_precedent = (adherents // PAS_CAP) * PAS_CAP
        prochain_cap = cap_precedent + PAS_CAP
        reste_avant_cap = prochain_cap - adherents
        jauge_txt = generer_jauge_texte(adherents, cap_precedent, prochain_cap, TAILLE_BARRE)

        cadence_txt, momentum_txt, moyenne_jour_7j = calculer_velocite(gain_24h, gain_7j)

        if moyenne_jour_7j > 0:
            jours_restants = reste_avant_cap / moyenne_jour_7j
            eta_date = maintenant + timedelta(days=jours_restants)
            eta_texte = f"~{int(eta_date.day)} {MOIS_FR[eta_date.month]} {eta_date.year}"
        else:
            eta_texte = "Indéterminée"

        # Tweet moderne, axé vélocité & chiffres clés
        tweet_texte = (
            f"📊 @nouv_energie · Baromètre d'adhésion\n\n"
            f"👥 {formater_nombre(adherents)} adhérents\n"
            f"⚡ Cadence : {cadence_txt}\n"
            f"📈 +{formater_nombre(gain_24h)} en 24h · +{formater_nombre(gain_7j)} sur 7j\n"
            f"{momentum_txt}\n\n"
            f"Objectif {prochain_cap // 1000}k :\n"
            f"{jauge_txt}\n"
            f"▫️ Reste : {formater_nombre(reste_avant_cap)} adhésions\n"
            f"▫️ Projection : {eta_texte}\n\n"
            f"#NouvelleEnergie #DavidLisnard"
        )

        email_corps = (
            f"Bonjour,\n\n"
            f"Le nouveau visuel statistique a été généré et est en pièce jointe (stats_card.png).\n\n"
            f"--- TWEET PRÊT À PUBLIER ---\n\n"
            f"{tweet_texte}\n\n"
            f"-----------------------------\n"
            f"Publier directement : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_texte)}"
        )

        # Génération du fichier PNG
        generer_carte_visuelle(
            adherents=adherents,
            gain_24h=gain_24h,
            gain_7j=gain_7j,
            cap_precedent=cap_precedent,
            prochain_cap=prochain_cap,
            output_path=CHART_FILE
        )

        envoyer_email(titre_sujet, email_corps, image_path=CHART_FILE)
        ecrire_etat(maintenant_iso, adherents, dernier_palier)
    else:
        print(f"Pas d'alerte requise ({adherents} adhérents).", flush=True)


if __name__ == "__main__":
    run()
