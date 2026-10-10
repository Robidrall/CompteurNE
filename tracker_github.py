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
from matplotlib.patches import FancyBboxPatch

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


def calculer_cadence_recente(csv_file: str = CSV_FILE) -> str:
    """Calcule la cadence moyenne entre les deux derniers enregistrements du CSV ramenée à la minute."""
    if not os.path.isfile(csv_file):
        return "Données insuffisantes"

    with open(csv_file, "r", encoding="utf-8") as f:
        lignes = [r for r in csv.reader(f) if r]

    # Il faut au moins l'en-tête et 2 relevés
    if len(lignes) < 3:
        return "Historique en cours de constitution"

    row_prec = lignes[-2]
    row_dernier = lignes[-1]

    try:
        dt_prec = datetime.fromisoformat(row_prec[0])
        dt_dernier = datetime.fromisoformat(row_dernier[0])
        adh_prec = int(row_prec[1])
        adh_dernier = int(row_dernier[1])
    except (ValueError, IndexError):
        return "Format invalide"

    delta_minutes = (dt_dernier - dt_prec).total_seconds() / 60
    delta_adh = adh_dernier - adh_prec

    if delta_minutes <= 0 or delta_adh <= 0:
        return "Rythme temporairement calme"

    adh_par_minute = delta_adh / delta_minutes
    if adh_par_minute >= 1.0:
        return f"~{adh_par_minute:.1f} adh. / min"

    minutes_par_adh = delta_minutes / delta_adh
    return f"1 adhésion toutes les {int(round(minutes_par_adh))} min"


def calculer_momentum(gain_24h: int, gain_7j: int):
    """Calcule la tendance d'accélération vs la moyenne hebdomadaire."""
    moyenne_jour = gain_7j / 7 if gain_7j > 0 else 0
    if moyenne_jour > 0:
        ecart = ((gain_24h - moyenne_jour) / moyenne_jour) * 100
        if ecart >= 10:
            return f"🟢 En accélération (+{int(ecart)} % vs moy. 7j)", moyenne_jour
        elif ecart <= -10:
            return f"🟠 En décélération ({int(ecart)} % vs moy. 7j)", moyenne_jour
        else:
            return "⚪ Rythme stable (conforme à la moy. 7j)", moyenne_jour
    return "⚪ Données en cours d'acquisition", moyenne_jour


def generer_carte_visuelle(adherents: int, gain_24h: int, gain_7j: int, 
                           cap_precedent: int, prochain_cap: int, output_path: str):
    """Génère une carte statistique 1200x675 px moderne aux couleurs de la France."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    fig = plt.figure(figsize=(12, 6.75), dpi=120, facecolor="#0B101E")
    
    # 1. Ruban tricolore supérieur (Bleu Marianne #002654, Blanc #FFFFFF, Rouge #ED2939)
    band_height = 0.014
    ax_banner = fig.add_axes([0, 1 - band_height, 1, band_height])
    ax_banner.axis("off")
    ax_banner.axvspan(0, 0.333, color="#002654")
    ax_banner.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_banner.axvspan(0.666, 1.0, color="#ED2939")
    
    # 2. Vignette drapeau tricolore
    ax_flag = fig.add_axes([0.07, 0.895, 0.024, 0.026])
    ax_flag.axis("off")
    ax_flag.axvspan(0, 0.333, color="#002654")
    ax_flag.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_flag.axvspan(0.666, 1.0, color="#ED2939")
    for spine in ax_flag.spines.values():
        spine.set_color("#334155")
        spine.set_visible(True)
        spine.set_linewidth(0.8)

    # 3. En-tête
    fig.text(0.105, 0.90, "NOUVELLE ÉNERGIE", color="#FFFFFF", fontsize=15, weight="heavy")
    fig.text(0.305, 0.90, "·  BAROMÈTRE D'ADHÉSION", color="#94A3B8", fontsize=14, weight="bold")
    
    # 4. Bloc principal du total
    fig.text(0.07, 0.77, formater_nombre(adherents), color="#FFFFFF", fontsize=52, weight="heavy")
    fig.text(0.46, 0.785, "adhérents", color="#38BDF8", fontsize=22, weight="bold")
    
    # 5. Bloc KPIs (24h et 7j)
    ax_bg = fig.add_axes([0.65, 0.75, 0.28, 0.16], facecolor="#131C31")
    ax_bg.axis("off")
    p_bbox = FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.04,rounding_size=0.15",
                            facecolor="#131C31", edgecolor="#1E2B4A", linewidth=1.2, transform=ax_bg.transAxes, clip_on=False)
    ax_bg.add_patch(p_bbox)
    ax_bg.text(0.12, 0.65, "DERNIÈRES 24H", color="#64748B", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.12, 0.25, f"+{formater_nombre(gain_24h)}", color="#10B981", fontsize=18, weight="heavy", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.65, "SUR 7 JOURS", color="#64748B", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.25, f"+{formater_nombre(gain_7j)}", color="#38BDF8", fontsize=18, weight="heavy", transform=ax_bg.transAxes)
    
    # 6. Progression vers le cap de 5k
    gain_tranche = adherents - cap_precedent
    total_tranche = prochain_cap - cap_precedent
    pct = int(min(100, max(0, (gain_tranche / total_tranche) * 100)))
    reste = prochain_cap - adherents
    
    txt_cap = f"Cap {prochain_cap // 1000}k  (tranche {cap_precedent // 1000}k -> {prochain_cap // 1000}k)"
    fig.text(0.07, 0.68, txt_cap, color="#F8FAFC", fontsize=13, weight="bold")
    fig.text(0.07, 0.64, f"{pct} % atteint (+{formater_nombre(gain_tranche)} / {formater_nombre(total_tranche)})   —   Reste {formater_nombre(reste)} adhésions", 
             color="#94A3B8", fontsize=11, weight="medium")
    
    # Barre de progression
    bar_x, bar_y, bar_w, bar_h = 0.07, 0.59, 0.86, 0.03
    bg_bar = FancyBboxPatch((bar_x, bar_y), bar_w, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
                            facecolor="#1A243D", edgecolor="#2D3B60", linewidth=1.0, transform=fig.transFigure, clip_on=False)
    fig.patches.append(bg_bar)
    
    w_fill = bar_w * (pct / 100.0)
    if w_fill > 0.01:
        fill_bar = FancyBboxPatch((bar_x, bar_y), w_fill, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
                                  facecolor="#0066FF", edgecolor="#60A5FA", linewidth=0.8, transform=fig.transFigure, clip_on=False)
        fig.patches.append(fill_bar)
        
    # 7. Courbe historique
    dates_hist, values_hist = [], []
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            for row in list(csv.reader(f))[1:]:
                try:
                    dates_hist.append(datetime.fromisoformat(row[0]))
                    values_hist.append(int(row[1]))
                except (ValueError, IndexError):
                    continue

    if len(dates_hist) >= 2:
        ax_curve = fig.add_axes([0.07, 0.12, 0.86, 0.38], facecolor="#0E162B")
        for spine in ax_curve.spines.values():
            spine.set_visible(False)
        ax_curve.spines["bottom"].set_visible(True)
        ax_curve.spines["bottom"].set_color("#223052")
        
        ax_curve.plot(dates_hist, values_hist, color="#0066FF", linewidth=3.2, zorder=4)
        ax_curve.scatter([dates_hist[-1]], [values_hist[-1]], color="#ED2939", s=65, zorder=5, edgecolor="#FFFFFF", linewidth=2)
        
        min_val = min(values_hist) - (max(values_hist) - min(values_hist)) * 0.15
        ax_curve.fill_between(dates_hist, values_hist, min_val, color="#0066FF", alpha=0.18, zorder=3)
        ax_curve.set_ylim(bottom=min_val)
        ax_curve.grid(axis="y", color="#1E2B4A", linestyle="--", alpha=0.6, zorder=1)
        ax_curve.tick_params(colors="#64748B", labelsize=10)
        ax_curve.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # 8. Pied de carte
    fig.text(0.07, 0.045, "Source : Données publiques adhérents · unenouvelleenergie.fr", color="#475569", fontsize=9.5, weight="medium")
    fig.text(0.79, 0.045, "@nouv_energie Tracker", color="#475569", fontsize=9.5, weight="bold")
    
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
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

    # Archivage CSV
    sauvegarder_csv(maintenant_iso, adherents, gain_7j)

    # Métriques
    gain_24h = calculer_gain_24h(maintenant, adherents)
    cadence_txt = calculer_cadence_recente(CSV_FILE)
    momentum_txt, moyenne_jour_7j = calculer_momentum(gain_24h, gain_7j)

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
        gain_depuis_palier = adherents - cap_precedent
        reste_avant_cap = prochain_cap - adherents

        jauge_txt = generer_jauge_texte(adherents, cap_precedent, prochain_cap, TAILLE_BARRE)

        # Projection (ETA)
        if moyenne_jour_7j > 0:
            jours_restants = reste_avant_cap / moyenne_jour_7j
            eta_date = maintenant + timedelta(days=jours_restants)
            eta_texte = f"~{int(eta_date.day)} {MOIS_FR[eta_date.month]} {eta_date.year}"
        else:
            eta_texte = "Indéterminée"

        tweet_texte = (
            f"📊 @nouv_energie · Baromètre d'adhésion\n\n"
            f"👥 {formater_nombre(adherents)} adhérents\n"
            f"⚡ Cadence : {cadence_txt}\n"
            f"📈 +{formater_nombre(gain_24h)} en 24h · +{formater_nombre(gain_7j)} sur 7j\n"
            f"{momentum_txt}\n\n"
            f"Objectif {prochain_cap // 1000}k (tranche {cap_precedent // 1000}k ➔ {prochain_cap // 1000}k) :\n"
            f"{jauge_txt} (+{formater_nombre(gain_depuis_palier)} / {formater_nombre(PAS_CAP)})\n"
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

        # Génération du visuel PNG
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
