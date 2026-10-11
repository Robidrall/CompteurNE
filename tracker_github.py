import csv
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import json
import os
import smtplib
import sys
import requests

import matplotlib
matplotlib.use("Agg")  # Mode headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch

# --- Paramètres généraux ---
PAS_PALIER = 25        # Alerte tous les 25 nouveaux adhérents
PAS_CAP = 5000         # Grands caps (35k, 40k, 45k...)
SEUIL_HEURES = 2       # Notification récurrente max
TAILLE_BARRE = 8       # Longueur de la jauge textuelle

# Nouvelles API ouvertes
URL_LIVE = "https://adherent.unenouvelleenergie.fr/donnees/adherents.json"
URL_HISTO = "https://adherent.unenouvelleenergie.fr/donnees/adherents/historique.json"

CSV_FILE = "data/adherents.csv"
STATE_FILE = "data/last_alert.json"
CHART_FILE = "data/stats_card.png"

SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")
DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", SMTP_USER)

MOIS_FR = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", 
           "juil.", "août", "sept.", "oct.", "nov.", "déc."]

JALONS_HISTORIQUES = [
    (datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc), 26500),
    (datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc), 29500),
    (datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc), 34651),
]

def formater_nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def generer_jauge_texte(actuel: int, palier_bas: int, palier_haut: int, taille: int = 10) -> str:
    progression = (actuel - palier_bas) / (palier_haut - palier_bas)
    progression = max(0.0, min(1.0, progression))
    pleins = int(round(progression * taille))
    vides = taille - pleins
    pct = int(progression * 100)
    return f"{'▓' * pleins}{'░' * vides} {pct} %"

def calculer_cadence_recente(csv_file: str = CSV_FILE) -> str:
    if not os.path.isfile(csv_file):
        return "Données insuffisantes"
    with open(csv_file, "r", encoding="utf-8") as f:
        lignes = [r for r in csv.reader(f) if r]
    if len(lignes) < 3:
        return "Historique en cours de constitution"

    row_prec, row_dernier = lignes[-2], lignes[-1]
    try:
        dt_prec = datetime.fromisoformat(row_prec[0])
        dt_dernier = datetime.fromisoformat(row_dernier[0])
        adh_prec, adh_dernier = int(row_prec[1]), int(row_dernier[1])
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

def extraire_donnees():
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp_live = requests.get(URL_LIVE, headers=headers, timeout=10)
        resp_live.raise_for_status()
        data_live = resp_live.json()
        adherents = data_live.get("adherents")
        mis_a_jour_str = data_live.get("mis_a_jour")

        try:
            date_maj = datetime.fromisoformat(mis_a_jour_str.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            date_maj = datetime.now(timezone.utc)

        resp_histo = requests.get(URL_HISTO, headers=headers, timeout=10)
        resp_histo.raise_for_status()
        jours = resp_histo.json().get("jours", [])

        # Gain 7j officiel (comparé au point d'il y a 7 jours arrêtés)
        gain_7j = 0
        if jours:
            idx_7j = -7 if len(jours) >= 7 else 0
            gain_7j = adherents - jours[idx_7j].get("adherents", adherents)

        return adherents, gain_7j, date_maj

    except requests.exceptions.RequestException as e:
        print(f"Erreur API : {e}")
        return None, None, None

def sauvegarder_csv(iso_date, adherents, gain_7j):
    """Conserve un historique local pour évaluer la cadence immédiate (calcul_cadence_recente)"""
    os.makedirs("data", exist_ok=True)
    existe = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["timestamp", "adherents", "gain_7j"])
        writer.writerow([iso_date, adherents, gain_7j])

def lire_etat():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def ecrire_etat(iso_date, adherents, dernier_palier):
    os.makedirs("data", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": iso_date,
            "adherents": adherents,
            "dernier_palier": dernier_palier
        }, f, indent=2)

def generer_carte_visuelle(
    adherents: int,
    gain_24h: int,
    gain_7j: int,
    cap_precedent: int,
    prochain_cap: int,
    output_path: str,
):
    """Carte 1 : Baromètre global sobre et lumineux aux couleurs de la France."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    fig = plt.figure(figsize=(12, 6.75), dpi=120, facecolor="#F8FAFC")

    # 1. Ruban tricolore supérieur
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
    for s in ax_flag.spines.values():
        s.set_color("#CBD5E1")
        s.set_linewidth(0.8)

    # 3. En-tête
    fig.text(0.105, 0.90, "NOUVELLE ÉNERGIE", color="#0F172A", fontsize=15, weight="heavy")
    fig.text(0.305, 0.90, "·  BAROMÈTRE D'ADHÉSION", color="#64748B", fontsize=14, weight="bold")

    # 4. Bloc principal du total
    fig.text(0.07, 0.77, formater_nombre(adherents), color="#0F172A", fontsize=52, weight="heavy")
    fig.text(0.46, 0.785, "adhérents", color="#0284C7", fontsize=22, weight="bold")

    # 5. Bloc KPIs
    ax_bg = fig.add_axes([0.65, 0.75, 0.28, 0.16], facecolor="#FFFFFF")
    ax_bg.axis("off")
    p_bbox = FancyBboxPatch(
        (0, 0), 1, 1, boxstyle="round,pad=0.04,rounding_size=0.15",
        facecolor="#FFFFFF", edgecolor="#E2E8F0", linewidth=1.2,
        transform=ax_bg.transAxes, clip_on=False,
    )
    ax_bg.add_patch(p_bbox)
    ax_bg.text(0.12, 0.65, "DERNIÈRES 24H", color="#64748B", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.12, 0.25, f"+{formater_nombre(gain_24h)}", color="#059669", fontsize=18, weight="heavy", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.65, "SUR 7 JOURS", color="#64748B", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.25, f"+{formater_nombre(gain_7j)}", color="#0284C7", fontsize=18, weight="heavy", transform=ax_bg.transAxes)

    # 6. Progression vers le cap
    gain_tranche = adherents - cap_precedent
    total_tranche = prochain_cap - cap_precedent
    pct = int(min(100, max(0, (gain_tranche / total_tranche) * 100)))
    reste = prochain_cap - adherents

    txt_cap = f"Cap {prochain_cap // 1000}k  (tranche {cap_precedent // 1000}k -> {prochain_cap // 1000}k)"
    fig.text(0.07, 0.68, txt_cap, color="#1E293B", fontsize=13, weight="bold")
    fig.text(
        0.07, 0.64,
        f"{pct} % atteint (+{formater_nombre(gain_tranche)} / {formater_nombre(total_tranche)})   —   Reste {formater_nombre(reste)} adhésions",
        color="#64748B", fontsize=11, weight="medium",
    )

    # Barre de progression
    bar_x, bar_y, bar_w, bar_h = 0.07, 0.59, 0.86, 0.03
    bg_bar = FancyBboxPatch(
        (bar_x, bar_y), bar_w, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
        facecolor="#E2E8F0", edgecolor="#CBD5E1", linewidth=1.0,
        transform=fig.transFigure, clip_on=False,
    )
    fig.patches.append(bg_bar)

    w_fill = bar_w * (pct / 100.0)
    if w_fill > 0.01:
        fill_bar = FancyBboxPatch(
            (bar_x, bar_y), w_fill, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
            facecolor="#0066FF", edgecolor="#2563EB", linewidth=0.8,
            transform=fig.transFigure, clip_on=False,
        )
        fig.patches.append(fill_bar)

    # 7. Chargement de l'historique complet
    points = list(JALONS_HISTORIQUES)
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            for row in list(csv.reader(f))[1:]:
                try:
                    points.append((datetime.fromisoformat(row[0]), int(row[1])))
                except (ValueError, IndexError):
                    continue

    points.sort(key=lambda x: x[0])
    dates_hist, values_hist = zip(*points)

    # Zone graphique
    ax_curve = fig.add_axes([0.07, 0.12, 0.86, 0.38], facecolor="#FFFFFF")
    for spine in ax_curve.spines.values():
        spine.set_color("#E2E8F0")
    ax_curve.spines["top"].set_visible(False)
    ax_curve.spines["right"].set_visible(False)

    # Tracé de la dynamique
    ax_curve.plot(dates_hist, values_hist, color="#0066FF", linewidth=3.0, zorder=4)

    # Marquage des jalons historiques spécifiques
    jalons_dt = [j[0] for j in JALONS_HISTORIQUES]
    jalons_val = [j[1] for j in JALONS_HISTORIQUES]
    ax_curve.scatter(jalons_dt, jalons_val, color="#002654", s=45, zorder=5, edgecolor="#FFFFFF", linewidth=1.8)

    # Point actuel
    ax_curve.scatter([dates_hist[-1]], [values_hist[-1]], color="#ED2939", s=65, zorder=6, edgecolor="#FFFFFF", linewidth=2)

    # Zone sous la courbe avec minimum adapté à fin août (~24 000)
    min_val = 24000
    ax_curve.fill_between(dates_hist, values_hist, min_val, color="#0066FF", alpha=0.08, zorder=3)
    ax_curve.set_ylim(bottom=min_val, top=max(values_hist) + 1500)

    ax_curve.grid(axis="y", color="#F1F5F9", linestyle="--", linewidth=1.2, zorder=1)
    ax_curve.tick_params(colors="#64748B", labelsize=10)
    ax_curve.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # 8. Pied de carte
    fig.text(0.07, 0.045, "Source : Données publiques adhérents · unenouvelleenergie.fr", color="#64748B", fontsize=9.5, weight="medium")
    fig.text(0.79, 0.045, "@CompteurNE", color="#64748B", fontsize=9.5, weight="bold")

    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)

def calculer_gain_24h(maintenant: datetime, adherents_actuels: int, csv_file: str = CSV_FILE) -> int:
    """Calcule le gain réel sur 24h glissantes par interpolation linéaire."""
    if not os.path.isfile(csv_file):
        return 0

    points = []
    with open(csv_file, "r", encoding="utf-8") as f:
        for row in list(csv.reader(f))[1:]:
            try:
                points.append((datetime.fromisoformat(row[0]), int(row[1])))
            except (ValueError, IndexError):
                continue

    if not points:
        return 0

    points.sort(key=lambda x: x[0])
    t_debut = points[0][0]
    cible = maintenant - timedelta(hours=24)

    # Moins de 24h d'historique dispo : prorata
    if cible < t_debut:
        heures_dispo = (maintenant - t_debut).total_seconds() / 3600
        if heures_dispo >= 1.0:
            gain_observe = adherents_actuels - points[0][1]
            return int(round((gain_observe / heures_dispo) * 24))
        return 0

    p_avant = None
    p_apres = None
    for pt in points:
        if pt[0] <= cible:
            p_avant = pt
        elif pt[0] > cible and p_apres is None:
            p_apres = pt
            break

    if p_avant and not p_apres:
        return adherents_actuels - p_avant[1]

    t1, y1 = p_avant
    t2, y2 = p_apres
    duree_segment = (t2 - t1).total_seconds()
    if duree_segment <= 0:
        return adherents_actuels - y1

    ratio = (cible - t1).total_seconds() / duree_segment
    adh_estimes_cible = y1 + ratio * (y2 - y1)
    return int(round(adherents_actuels - adh_estimes_cible))

def envoyer_email(sujet, contenu, image_path):
    msg = MIMEMultipart()
    msg["Subject"] = sujet
    msg["From"] = SMTP_USER
    msg["To"] = DESTINATAIRE

    msg.attach(MIMEText(contenu, "plain", "utf-8"))

    if os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(image_path)}"')
            msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
    print(f"E-mail envoyé avec succès à {DESTINATAIRE}", flush=True)

def run():
    if not all([SMTP_USER, SMTP_PASS]):
        print("Erreur : identifiants SMTP manquants.", flush=True)
        sys.exit(1)

    # 1. Dépaqueter exactement les 3 valeurs renvoyées par extraire_donnees()
    adherents, gain_7j, date_maj = extraire_donnees()
    if adherents is None:
        print("Impossible d'extraire les données.", flush=True)
        sys.exit(1)

    maintenant = date_maj
    maintenant_iso = maintenant.isoformat()

    # 2. Sauvegarde dans le CSV
    sauvegarder_csv(maintenant_iso, adherents, gain_7j)

    # 3. Calculer gain_24h à partir de l'historique CSV
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
        titre_sujet = f"📊 Baromètre : {formater_nombre(adherents)} adhérents"

    if doit_notifier:
        cap_precedent = (adherents // PAS_CAP) * PAS_CAP
        prochain_cap = cap_precedent + PAS_CAP
        reste_avant_cap = prochain_cap - adherents

        jauge_txt = generer_jauge_texte(adherents, cap_precedent, prochain_cap, TAILLE_BARRE)

        if moyenne_jour_7j > 0:
            jours_restants = reste_avant_cap / moyenne_jour_7j
            eta_date = maintenant + timedelta(days=jours_restants)
            eta_texte = f"~{int(eta_date.day)} {MOIS_FR[eta_date.month]} {eta_date.year}"
        else:
            eta_texte = "Indéterminée"

        tweet_texte = (
            f"📊 @nouv_energie · Baromètre\n\n"
            f"👥 {formater_nombre(adherents)} adhérents\n\n"
            f"⚡ {cadence_txt}\n"
            f"📈 +{formater_nombre(gain_24h)} (24h) · +{formater_nombre(gain_7j)} (7j)\n"
            f"{momentum_txt}\n\n"
            f"Cap {cap_precedent // 1000}k ➔ {prochain_cap // 1000}k ({jauge_txt}) :\n"
            f"▫️ Reste : {formater_nombre(reste_avant_cap)} adhésions\n"
            f"▫️ Projection : {eta_texte}"
        )

        generer_carte_visuelle(adherents, gain_24h, gain_7j, cap_precedent, prochain_cap, CHART_FILE)

        email_corps = (
            f"Bonjour,\n\n"
            f"Nouveau point d'étape disponible. Le visuel est en pièce jointe :\n"
            f"- stats_card.png (Baromètre)\n\n"
            f"===================================================\n"
            f"--- TWEET ---\n"
            f"===================================================\n"
            f"{tweet_texte}\n\n"
            f"Lien : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_texte)}\n\n"
        )

        envoyer_email(titre_sujet, email_corps, CHART_FILE)
        ecrire_etat(maintenant_iso, adherents, dernier_palier)
    else:
        print(f"Pas d'alerte requise ({adherents} adhérents).", flush=True)

if __name__ == "__main__":
    run()
