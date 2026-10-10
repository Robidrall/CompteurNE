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
import numpy as np
import requests

import matplotlib
matplotlib.use("Agg")  # Mode headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch

# --- Paramètres généraux ---
PAS_PALIER = 25        # Alerte tous les 50 nouveaux adhérents
PAS_CAP = 5000         # Grands caps (35k, 40k, 45k...)
SEUIL_HEURES = 2       # Notification récurrente max
TAILLE_BARRE = 8      # Longueur de la jauge textuelle

URL = "https://adherent.unenouvelleenergie.fr/parrainer"
LIEN_ADHESION = "https://soutenir.unenouvelleenergie.fr/p/SZZQKUQB"
CSV_FILE = "data/adherents.csv"
STATE_FILE = "data/last_alert.json"
CHART_FILE = "data/stats_card.png"
CHART_12H_FILE = "data/stats_vitesse_12h.png"

COOKIE = os.getenv("EA_SESSION")
SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")
DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", SMTP_USER)

MOIS_FR = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", 
           "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def formater_nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def generer_jauge_texte(actuel: int, palier_bas: int, palier_haut: int, taille: int = 10) -> str:
    progression = (actuel - palier_bas) / (palier_haut - palier_bas)
    progression = max(0.0, min(1.0, progression))
    pleins = int(round(progression * taille))
    vides = taille - pleins
    pct = int(progression * 100)
    return f"{'▓' * pleins}{'░' * vides} {pct} %"

import scipy.optimize as opt


def ajuster_polynome_croissant(x: np.ndarray, y: np.ndarray):
    """Ajuste f(t) = at² + bt + c sous la contrainte stricte f'(t) >= 0 sur tout l'intervalle."""
    t_fin = x[-1]

    # Test initial avec moindres carrés ordinaires
    poly = np.polyfit(x, y, deg=2)
    a, b, c = poly

    # Si la dérivée est déjà positive partout, on conserve la solution optimale
    if b >= 0 and (2 * a * t_fin + b) >= 0:
        return a, b, c

    # Sinon, optimisation sous contraintes : min ||pred - y||²
    def objectif(p):
        return np.sum((p[0] * x**2 + p[1] * x + p[2] - y) ** 2)

    # Contraintes : f'(0) >= 0 et f'(t_fin) >= 0
    contraintes = [
        {"type": "ineq", "fun": lambda p: p[1]},
        {"type": "ineq", "fun": lambda p: 2 * p[0] * t_fin + p[1]},
    ]

    # Estimation initiale basée sur une régression linéaire positive
    p1 = np.polyfit(x, y, 1)
    p_init = [0.0, max(0.0, p1[0]), p1[1]]

    res = opt.minimize(
        objectif, p_init, constraints=contraintes, method="SLSQP"
    )
    if res.success:
        return res.x[0], res.x[1], res.x[2]

    # Solution de repli (régression linéaire à pente positive)
    return 0.0, max(0.0, p1[0]), p1[1]


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


def generer_carte_visuelle(adherents: int, gain_24h: int, gain_7j: int, 
                           cap_precedent: int, prochain_cap: int, output_path: str):
    """Carte 1 : Baromètre global avec un fond très clair, sobre et institutionnel."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    
    # 1. Figure et couleur de fond claire / sobre (gris très clair)
    fig = plt.figure(figsize=(12, 6.75), dpi=120, facecolor="#F4F5F7")
    
    # Ruban tricolore
    ax_banner = fig.add_axes([0, 0.986, 1, 0.014])
    ax_banner.axis("off")
    ax_banner.axvspan(0, 0.333, color="#002654")
    ax_banner.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_banner.axvspan(0.666, 1.0, color="#ED2939")
    
    # Badge tricolore
    ax_flag = fig.add_axes([0.07, 0.895, 0.024, 0.026])
    ax_flag.axis("off")
    ax_flag.axvspan(0, 0.333, color="#002654")
    ax_flag.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_flag.axvspan(0.666, 1.0, color="#ED2939")
    for s in ax_flag.spines.values():
        s.set_color("#BDC3C7")
        s.set_linewidth(0.8)

    # 2. En-tête (Textes sombres)
    fig.text(0.105, 0.90, "NOUVELLE ÉNERGIE", color="#0A192F", fontsize=15, weight="heavy")
    fig.text(0.305, 0.90, "·  BAROMÈTRE D'ADHÉSION", color="#5A6B82", fontsize=14, weight="bold")
    
    # 3. Affichage du chiffre (Bleu nuit profond / bleu roi discret)
    fig.text(0.07, 0.77, formater_nombre(adherents), color="#0A192F", fontsize=52, weight="heavy")
    fig.text(0.46, 0.785, "adhérents", color="#0066FF", fontsize=22, weight="bold")
    
    # 4. Bloc KPIs (Glassmorphism clair / gris doux)
    ax_bg = fig.add_axes([0.65, 0.75, 0.28, 0.16], facecolor="#EBF0F5")
    ax_bg.axis("off")
    p_bbox = FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.04,rounding_size=0.15",
                            facecolor="#EBF0F5", edgecolor="#CFD8E1", linewidth=1.2, transform=ax_bg.transAxes, clip_on=False)
    ax_bg.add_patch(p_bbox)
    ax_bg.text(0.12, 0.65, "DERNIÈRES 24H", color="#5A6B82", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.12, 0.25, f"+{formater_nombre(gain_24h)}", color="#0F8A5F", fontsize=18, weight="heavy", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.65, "SUR 7 JOURS", color="#5A6B82", fontsize=9, weight="bold", transform=ax_bg.transAxes)
    ax_bg.text(0.58, 0.25, f"+{formater_nombre(gain_7j)}", color="#0066FF", fontsize=18, weight="heavy", transform=ax_bg.transAxes)
    
    # 5. Progression (Camaïeu de gris et bleu roi)
    gain_tranche = adherents - cap_precedent
    total_tranche = prochain_cap - cap_precedent
    pct = int(min(100, max(0, (gain_tranche / total_tranche) * 100)))
    reste = prochain_cap - adherents
    
    txt_cap = f"Cap {prochain_cap // 1000}k  (tranche {cap_precedent // 1000}k -> {prochain_cap // 1000}k)"
    fig.text(0.07, 0.68, txt_cap, color="#0A192F", fontsize=13, weight="bold")
    fig.text(0.07, 0.64, f"{pct} % atteint (+{formater_nombre(gain_tranche)} / {formater_nombre(total_tranche)})   —   Reste {formater_nombre(reste)} adhésions", 
             color="#5A6B82", fontsize=11, weight="medium")
    
    # Barres de progression avec couleurs douces adaptées au fond clair
    bar_x, bar_y, bar_w, bar_h = 0.07, 0.59, 0.86, 0.03
    bg_bar = FancyBboxPatch((bar_x, bar_y), bar_w, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
                            facecolor="#E2E8F0", edgecolor="#CBD5E1", linewidth=1.0, transform=fig.transFigure, clip_on=False)
    fig.patches.append(bg_bar)
    
    w_fill = bar_w * (pct / 100.0)
    if w_fill > 0.01:
        fill_bar = FancyBboxPatch((bar_x, bar_y), w_fill, bar_h, boxstyle="round,pad=0.003,rounding_size=0.015",
                                  facecolor="#0066FF", edgecolor="#3B82F6", linewidth=0.8, transform=fig.transFigure, clip_on=False)
        fig.patches.append(fill_bar)
        
    dates_hist, values_hist = [], []
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            for row in list(csv.reader(f))[1:]:
                try:
                    dates_hist.append(datetime.fromisoformat(row[0]))
                    values_hist.append(int(row[1]))
                except (ValueError, IndexError):
                    continue

    # 6. Graphe (Ajustement avec des tons clairs et légers)
    if len(dates_hist) >= 2:
        ax_curve = fig.add_axes([0.07, 0.12, 0.86, 0.38], facecolor="#FFFFFF")
        for spine in ax_curve.spines.values():
            spine.set_visible(False)
        ax_curve.spines["bottom"].set_visible(True)
        ax_curve.spines["bottom"].set_color("#CBD5E1")
        
        # Courbe principale bleu roi
        ax_curve.plot(dates_hist, values_hist, color="#0066FF", linewidth=3.2, zorder=4)
        
        # Dernier point (accent rouge)
        ax_curve.scatter([dates_hist[-1]], [values_hist[-1]], color="#ED2939", s=65, zorder=5, edgecolor="#FFFFFF", linewidth=2)
        
        # Remplissage dégradé bleu translucide
        min_val = min(values_hist) - (max(values_hist) - min(values_hist)) * 0.15
        ax_curve.fill_between(dates_hist, values_hist, min_val, color="#0066FF", alpha=0.08, zorder=3)
        ax_curve.set_ylim(bottom=min_val)
        
        # Grilles claires
        ax_curve.grid(axis="y", color="#E2E8F0", linestyle="--", alpha=0.8, zorder=1)
        ax_curve.tick_params(colors="#475569", labelsize=10)
        ax_curve.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # Footers
    fig.text(0.07, 0.045, "Source : Données publiques adhérents · unenouvelleenergie.fr", color="#5A6B82", fontsize=9.5, weight="medium")
    fig.text(0.79, 0.045, "@CompteurNE", color="#5A6B82", fontsize=9.5, weight="bold")
    
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def generer_carte_vitesse_12h(output_path: str = CHART_12H_FILE):
    """Carte 2 : Analyse 12h avec cohérence stricte entre la formule, le texte et les courbes."""
    if not os.path.isfile(CSV_FILE):
        return 0, 0, "f(t) indisponible", "f'(t) indisponible"

    dates_raw, adh_raw = [], []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        for r in list(csv.reader(f))[1:]:
            try:
                dates_raw.append(datetime.fromisoformat(r[0]))
                adh_raw.append(int(r[1]))
            except (ValueError, IndexError):
                continue

    if len(dates_raw) < 3:
        return 0, 0, "f(t) indisponible", "f'(t) indisponible"

    t_max = dates_raw[-1]
    t_min_12h = t_max - timedelta(hours=12)

    # 1. Sélection des points sur la fenêtre de 12 heures
    points_12h = [(d, v) for d, v in zip(dates_raw, adh_raw) if d >= t_min_12h]
    if len(points_12h) < 4:
        points_12h = list(zip(dates_raw[-8:], adh_raw[-8:]))

    d_12h, v_12h = zip(*points_12h)
    t0 = d_12h[0]
    x_hours = np.array([(d - t0).total_seconds() / 3600.0 for d in d_12h])
    y_vals = np.array(v_12h)

    # 2. Modélisation UNIQUE (utilisée à la fois pour les formules, le badge KPI et les courbes)
    poly = np.polyfit(x_hours, y_vals, deg=2)
    a, b, c = ajuster_polynome_croissant(x_hours, y_vals)
    p = np.poly1d([a, b, c])
    p_deriv = p.deriv()  # Dérivée f'(t) = 2at + b en adhérents/heure

    # Vitesse calculée au dernier point (t_fin)
    t_fin = x_hours[-1]
    vitesse_actuelle_h = max(0, p_deriv(t_fin))
    gain_total_12h = y_vals[-1] - y_vals[0]

    # Formules textuelles affichées
    b_sign = "+" if b >= 0 else "-"
    c_sign = "+" if c >= 0 else "-"
    formule_ft = f"f(t) = {a:+.2f}t² {b_sign} {abs(b):.1f}t {c_sign} {abs(int(c)):,}".replace(",", " ")
    formule_fprime = f"f'(t) = {2*a:+.2f}t {b_sign} {abs(b):.1f}"

    # Grille haute résolution pour le tracé (rigoureusement identique aux formules)
    grid_x = np.linspace(0, t_fin, 200)
    grid_dt = [t0 + timedelta(hours=h) for h in grid_x]
    grid_y = p(grid_x)
    grid_dy = np.maximum(0, p_deriv(grid_x))

    # 3. Rendu Figure
    fig = plt.figure(figsize=(12, 6.75), dpi=120, facecolor="#0B101E")

    # Ruban et badge tricolore
    ax_banner = fig.add_axes([0, 0.986, 1, 0.014])
    ax_banner.axis("off")
    ax_banner.axvspan(0, 0.333, color="#002654")
    ax_banner.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_banner.axvspan(0.666, 1.0, color="#ED2939")

    ax_flag = fig.add_axes([0.07, 0.895, 0.024, 0.026])
    ax_flag.axis("off")
    ax_flag.axvspan(0, 0.333, color="#002654")
    ax_flag.axvspan(0.333, 0.666, color="#FFFFFF")
    ax_flag.axvspan(0.666, 1.0, color="#ED2939")
    for s in ax_flag.spines.values():
        s.set_color("#334155")
        s.set_linewidth(0.8)

    fig.text(0.105, 0.90, "NOUVELLE ÉNERGIE", color="#FFFFFF", fontsize=15, weight="heavy")
    fig.text(0.305, 0.90, "·  ANALYSE DYNAMIQUE (12H)", color="#94A3B8", fontsize=14, weight="bold")

    # Boîte KPIs droite
    ax_kpi = fig.add_axes([0.62, 0.77, 0.31, 0.14], facecolor="#131C31")
    ax_kpi.axis("off")
    p_box = FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.04,rounding_size=0.15",
                           facecolor="#131C31", edgecolor="#1E2B4A", linewidth=1.2, transform=ax_kpi.transAxes, clip_on=False)
    ax_kpi.add_patch(p_box)
    ax_kpi.text(0.08, 0.65, "GAIN SUR 12H", color="#64748B", fontsize=9, weight="bold", transform=ax_kpi.transAxes)
    ax_kpi.text(0.08, 0.22, f"+{formater_nombre(gain_total_12h)}", color="#10B981", fontsize=18, weight="heavy", transform=ax_kpi.transAxes)
    ax_kpi.text(0.55, 0.65, "VITESSE ACTUELLE", color="#64748B", fontsize=9, weight="bold", transform=ax_kpi.transAxes)
    ax_kpi.text(0.55, 0.22, f"~{int(vitesse_actuelle_h)} adh/h", color="#38BDF8", fontsize=18, weight="heavy", transform=ax_kpi.transAxes)

    # Formules écrites en titre
    fig.text(0.07, 0.81, "Modélisation : f(t) [cumul] & f'(t) [vitesse]", color="#FFFFFF", fontsize=18, weight="bold")
    fig.text(0.07, 0.75, f"{formule_ft}   |   {formule_fprime}", color="#38BDF8", fontsize=12, weight="bold")

    # Graphe 1 : f(t)
    ax1 = fig.add_axes([0.07, 0.44, 0.86, 0.26], facecolor="#0E162B")
    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax1.spines["bottom"].set_visible(True)
    ax1.spines["bottom"].set_color("#223052")
    ax1.plot(grid_dt, grid_y, color="#0066FF", linewidth=2.8, zorder=4)
    ax1.scatter(d_12h, v_12h, color="#FFFFFF", s=30, zorder=5, edgecolor="#0066FF", linewidth=1.5)
    ax1.scatter([d_12h[-1]], [v_12h[-1]], color="#ED2939", s=60, zorder=6, edgecolor="#FFFFFF", linewidth=2)
    ax1.fill_between(grid_dt, grid_y, min(grid_y) - 20, color="#0066FF", alpha=0.15, zorder=3)
    ax1.set_ylim(bottom=min(grid_y) - 20)
    ax1.set_ylabel("f(t) Adhérents", color="#94A3B8", fontsize=10, weight="bold")
    ax1.tick_params(colors="#64748B", labelsize=9)
    ax1.grid(axis="y", color="#1E2B4A", linestyle="--", alpha=0.5)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    # Graphe 2 : f'(t)
    ax2 = fig.add_axes([0.07, 0.12, 0.86, 0.24], facecolor="#0E162B")
    for spine in ax2.spines.values():
        spine.set_visible(False)
    ax2.spines["bottom"].set_visible(True)
    ax2.spines["bottom"].set_color("#223052")
    ax2.plot(grid_dt, grid_dy, color="#10B981", linewidth=2.5, zorder=4)
    ax2.fill_between(grid_dt, grid_dy, 0, color="#10B981", alpha=0.18, zorder=3)
    # Point terminal aligné
    ax2.scatter([grid_dt[-1]], [grid_dy[-1]], color="#ED2939", s=55, zorder=6, edgecolor="#FFFFFF", linewidth=2)
    ax2.set_ylabel("f'(t) adh/h", color="#10B981", fontsize=10, weight="bold")
    ax2.tick_params(colors="#64748B", labelsize=9)
    ax2.grid(axis="y", color="#1E2B4A", linestyle="--", alpha=0.5)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    fig.text(0.07, 0.045, "Source : Données publiques · Modélisation polynomiale d'afflux (t en heures)", color="#475569", fontsize=9.5, weight="medium")
    fig.text(0.79, 0.045, "@CompteurNE", color="#475569", fontsize=9.5, weight="bold")

    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)

    return gain_total_12h, vitesse_actuelle_h, formule_ft, formule_fprime


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
    """Calcule le gain sur 24h glissantes par interpolation linéaire

    pour corriger les intervalles de relevé irréguliers (2h, 3h, etc.).
    """
    if not os.path.isfile(CSV_FILE):
        return 0

    points = []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        for row in list(csv.reader(f))[1:]:
            try:
                points.append((datetime.fromisoformat(row[0]), int(row[1])))
            except (ValueError, IndexError):
                continue

    if not points:
        return 0

    # Tri par sécurité
    points.sort(key=lambda x: x[0])

    t_debut = points[0][0]
    cible = maintenant - timedelta(hours=24)

    # Cas 1 : Moins de 24h d'historique dans le fichier -> prorata sur 24h
    if cible < t_debut:
        heures_dispo = (maintenant - t_debut).total_seconds() / 3600
        if heures_dispo >= 1.0:
            gain_observe = adherents_actuels - points[0][1]
            return int(round((gain_observe / heures_dispo) * 24))
        return 0

    # Cas 2 : Recherche des deux points qui encadrent exactement la cible
    p_avant = None
    p_apres = None

    for pt in points:
        if pt[0] <= cible:
            p_avant = pt
        elif pt[0] > cible and p_apres is None:
            p_apres = pt
            break

    # Si la cible tombe pile sur un relevé ou au-delà du dernier connu
    if p_avant and not p_apres:
        return adherents_actuels - p_avant[1]

    # Interpolation linéaire exacte entre p_avant et p_apres
    t1, y1 = p_avant
    t2, y2 = p_apres

    duree_segment = (t2 - t1).total_seconds()
    if duree_segment <= 0:
        return adherents_actuels - y1

    ratio = (cible - t1).total_seconds() / duree_segment
    adh_estimes_cible = y1 + ratio * (y2 - y1)

    return int(round(adherents_actuels - adh_estimes_cible))


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


def envoyer_email(sujet, contenu, images=None):
    msg = MIMEMultipart()
    msg["Subject"] = sujet
    msg["From"] = SMTP_USER
    msg["To"] = DESTINATAIRE

    msg.attach(MIMEText(contenu, "plain", "utf-8"))

    if images:
        for img_path in images:
            if os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(img_path)}"')
                    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
    print(f"E-mail (avec les 2 visuels) envoyé avec succès à {DESTINATAIRE}", flush=True)


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
        titre_sujet = f"📊 Baromètre 6h : {formater_nombre(adherents)} adhérents"

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

        # Tweet 1 : Baromètre principal (aéré avec @nouv_energie)
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

        # Génération des 2 visuels
        generer_carte_visuelle(adherents, gain_24h, gain_7j, cap_precedent, prochain_cap, CHART_FILE)
        gain_12h, v_h, formule_ft, formule_fprime = generer_carte_vitesse_12h(CHART_12H_FILE)

        # Tweet 2 : Analyse 12h, formules mathématiques et lien d'adhésion
        tweet_2 = (
            f"⚡ @nouv_energie · Demi-journée (12h)\n\n"
            f"📈 +{formater_nombre(gain_12h)} en 12h (vitesse : ~{int(v_h)} adh/h)\n\n"
            f"📐 {formule_ft}\n"
            f"⚡ {formule_fprime}\n\n"
            f"Plus que {formater_nombre(reste_avant_cap)} avant les {prochain_cap // 1000}k ! Booster f'(t) 👇\n"
            f"{LIEN_ADHESION}"
        )

        email_corps = (
            f"Bonjour,\n\n"
            f"Nouveau point d'étape disponible. Les 2 visuels sont en pièces jointes :\n"
            f"1. stats_card.png (Baromètre)\n"
            f"2. stats_vitesse_12h.png (Dynamique 12h f(t) / f'(t))\n\n"
            f"===================================================\n"
            f"--- TWEET 1 (Baromètre + stats_card.png) ---\n"
            f"===================================================\n"
            f"{tweet_texte}\n\n"
            f"Lien : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_texte)}\n\n"
            f"===================================================\n"
            f"--- TWEET 2 (Réponse au 1er + stats_vitesse_12h.png) ---\n"
            f"===================================================\n"
            f"{tweet_2}\n\n"
            f"Lien : https://twitter.com/intent/tweet?text={requests.utils.quote(tweet_2)}"
        )

        envoyer_email(titre_sujet, email_corps, images=[CHART_FILE, CHART_12H_FILE])
        ecrire_etat(maintenant_iso, adherents, dernier_palier)
    else:
        print(f"Pas d'alerte requise ({adherents} adhérents).", flush=True)


if __name__ == "__main__":
    run()
