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
from playwright.sync_api import sync_playwright

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

# Jalons sur l'ensemble de l'année 2026 pour illustrer l'accélération fulgurante récente
JALONS_HISTORIQUES = [
    (datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), 16500),
    (datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc), 18200),
    (datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc), 20400),
    (datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc), 23100),
    (datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc), 26500),
    (datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc), 29500),
    (datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc), 34651),
]

# Heure de Paris (UTC+2 en été, UTC+1 en hiver)
TZ_FR = timezone(timedelta(hours=2))

def formater_nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def generer_jauge_texte(actuel: int, palier_bas: int, palier_haut: int, taille: int = 10) -> str:
    progression = (actuel - palier_bas) / (palier_haut - palier_bas)
    progression = max(0.0, min(1.0, progression))
    pleins = int(round(progression * taille))
    vides = taille - pleins
    pct = int(progression * 100)
    return f"{'▓' * pleins}{'░' * vides} {pct} %"

def calculer_cadence_recente(csv_file: str = CSV_FILE) -> tuple[str, str]:
    """
    Retourne un tuple :
    1. Le texte complet pour le tweet (h + min + plage horaire)
    2. La valeur courte pour la 3e carte KPI du visuel (ex: '~201' ou '~4.5')
    """
    if not os.path.isfile(csv_file):
        return "Données insuffisantes", "—"
    with open(csv_file, "r", encoding="utf-8") as f:
        lignes = [r for r in csv.reader(f) if r]
    if len(lignes) < 3:
        return "Historique en cours de constitution", "—"

    row_prec, row_dernier = lignes[-2], lignes[-1]
    try:
        dt_prec = datetime.fromisoformat(row_prec[0]).astimezone(TZ_FR)
        dt_dernier = datetime.fromisoformat(row_dernier[0]).astimezone(TZ_FR)
        adh_prec, adh_dernier = int(row_prec[1]), int(row_dernier[1])
    except (ValueError, IndexError):
        return "Format invalide", "—"

    delta_sec = (dt_dernier - dt_prec).total_seconds()
    delta_heures = delta_sec / 3600
    delta_minutes = delta_sec / 60
    delta_adh = adh_dernier - adh_prec

    h_debut = dt_prec.strftime("%Hh%M")
    h_fin = dt_dernier.strftime("%Hh%M")
    duree_txt = f"{int(round(delta_minutes))} min" if delta_minutes < 60 else f"{int(delta_heures)}h{int(delta_minutes % 60):02d}"
    contexte = f"de {h_debut} à {h_fin} ({duree_txt})"

    if delta_heures <= 0 or delta_adh <= 0:
        return f"Rythme calme · {contexte}", "0"

    adh_par_heure = delta_adh / delta_heures
    adh_par_minute = delta_adh / delta_minutes

    # Formatage du volet horaire
    if adh_par_heure >= 10:
        val_kpi = f"~{formater_nombre(int(round(adh_par_heure)))}"
        txt_heure = f"{val_kpi} adh. / h"
    else:
        val_kpi = f"~{adh_par_heure:.1f}"
        txt_heure = f"{val_kpi} adh. / h"

    # Formatage du volet minute
    if adh_par_minute >= 1.0:
        txt_min = f"~{adh_par_minute:.1f} adh. / min"
    else:
        min_par_adh = delta_minutes / delta_adh
        txt_min = f"1 adh. / {int(round(min_par_adh))} min"

    return f"{txt_heure} ({txt_min}) · {contexte}", val_kpi

def calculer_momentum(gain_24h: int, gain_7j: int):
    moyenne_jour = gain_7j / 7 if gain_7j > 0 else 0
    if moyenne_jour > 0:
        ecart = ((gain_24h - moyenne_jour) / moyenne_jour) * 100
        if ecart >= 10:
            return f"🟢 En accélération (+{int(ecart)} % vs moy. 7j)", moyenne_jour, "EN ACCÉLÉRATION"
        elif ecart <= -10:
            return f"🟠 En décélération ({int(ecart)} % vs moy. 7j)", moyenne_jour, "RYTHME MODÉRÉ"
        else:
            return "⚪ Rythme stable (conforme à la moy. 7j)", moyenne_jour, "RYTHME STABLE"
    return "⚪ Données en cours d'acquisition", moyenne_jour, "DYNAMIQUE SUR 7 JOURS"

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

        gain_7j = 0
        points_api = []
        if jours:
            idx_7j = -7 if len(jours) >= 7 else 0
            gain_7j = adherents - jours[idx_7j].get("adherents", adherents)
            # Récupération de tous les points historiques fournis par l'API officielle
            for j in jours:
                d_str = j.get("date") or j.get("jour") or j.get("timestamp")
                val = j.get("adherents")
                if d_str and val is not None:
                    try:
                        if "T" in str(d_str):
                            dt_j = datetime.fromisoformat(str(d_str).replace("Z", "+00:00"))
                        else:
                            dt_j = datetime.fromisoformat(f"{d_str}T12:00:00+00:00")
                        points_api.append((dt_j, int(val)))
                    except ValueError:
                        continue

        return adherents, gain_7j, date_maj, points_api

    except requests.exceptions.RequestException as e:
        print(f"Erreur API : {e}")
        return None, None, None, []

def sauvegarder_csv(iso_date, adherents, gain_7j):
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
    cadence_kpi: str,
    badge_momentum: str,
    cap_precedent: int,
    prochain_cap: int,
    eta_court: str,
    maintenant: datetime,
    points_api: list,
    output_path: str,
):
    """
    Génère la carte visuelle sur l'année entière avec mois en filigrane,
    séparations verticales et axes grand format.
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    # Fusion des jalons annuels + historique API + CSV local
    points = list(JALONS_HISTORIQUES) + list(points_api)
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            for row in list(csv.reader(f))[1:]:
                try:
                    points.append((datetime.fromisoformat(row[0]), int(row[1])))
                except (ValueError, IndexError):
                    continue

    points.sort(key=lambda x: x[0])
    
    unique_points = {}
    for dt, val in points:
        unique_points[int(dt.timestamp() * 1000)] = val

    chart_data = []
    sorted_ts = sorted(unique_points.keys())
    for i, ts in enumerate(sorted_ts):
        chart_data.append({
            "x": ts,
            "y": unique_points[ts],
            "isLast": (i == len(sorted_ts) - 1)
        })

    gain_tranche = adherents - cap_precedent
    total_tranche = prochain_cap - cap_precedent
    pct = min(100.0, max(0.0, (gain_tranche / total_tranche) * 100))
    pct_int = int(pct)
    reste = prochain_cap - adherents

    dt_fr = maintenant.astimezone(TZ_FR)
    date_header = f"{dt_fr.day} {MOIS_FR[dt_fr.month].upper()} {dt_fr.year}"

    min_y_val = min(unique_points.values()) if unique_points else 15000
    min_y_axis = max(0, ((min_y_val - 2000) // 5000) * 5000)
    max_y = max(unique_points.values()) if unique_points else adherents
    suggested_max_y = ((max_y // 5000) + 1) * 5000

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800;900&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1200px;
    height: 675px;
    font-family: 'Inter', -apple-system, sans-serif;
    background: linear-gradient(135deg, #F8FAFC 0%, #EFF6FF 100%);
    color: #0F172A;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
    position: relative;
  }}

  .top-banner {{
    height: 8px;
    width: 100%;
    display: flex;
    position: relative;
    z-index: 2;
  }}
  .top-banner div {{ flex: 1; }}
  .b-blue {{ background: #002654; }}
  .b-white {{ background: #FFFFFF; }}
  .b-red {{ background: #ED2939; }}

  .container {{
    padding: 24px 52px 20px 52px;
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    position: relative;
    z-index: 1;
  }}

  .top-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .eyebrow {{
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 2px;
    color: #0284C7;
    text-transform: uppercase;
  }}
  .pill-badge {{
    display: inline-flex;
    align-items: center;
    gap: 10px;
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid #CBD5E1;
    padding: 8px 18px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 1px;
    color: #0F172A;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
  }}
  .pill-dot {{
    width: 10px;
    height: 10px;
    background: #ED2939;
    border-radius: 50%;
    box-shadow: 0 0 0 4px rgba(237, 41, 57, 0.2);
  }}

  .hero-block {{
    margin-top: -8px;
  }}
  .total-val {{
    font-size: 84px;
    font-weight: 900;
    letter-spacing: -3px;
    line-height: 0.96;
    background: linear-gradient(95deg, #0F172A 0%, #1E3A8A 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .total-sub {{
    font-size: 23px;
    font-weight: 700;
    color: #334155;
    margin-top: 3px;
  }}

  .kpi-row {{
    display: flex;
    gap: 16px;
  }}
  .kpi-card {{
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid #CBD5E1;
    border-radius: 16px;
    padding: 12px 24px;
    width: 230px;
    box-shadow: 0 8px 20px -5px rgba(15, 23, 42, 0.05);
  }}
  .kpi-val {{
    font-size: 30px;
    font-weight: 900;
    letter-spacing: -0.8px;
    line-height: 1.1;
  }}
  .val-24h {{ color: #059669; }}
  .val-7j {{ color: #0284C7; }}
  .val-rate {{ color: #0F172A; }}
  .kpi-label {{
    font-size: 11.5px;
    font-weight: 800;
    color: #64748B;
    letter-spacing: 0.9px;
    text-transform: uppercase;
    margin-top: 4px;
  }}

  /* Zone Graphique avec cadre subtil pour faire ressortir les bandes de mois */
  .chart-wrapper {{
    position: relative;
    height: 235px;
    width: 100%;
    background: rgba(255, 255, 255, 0.75);
    border: 1px solid #CBD5E1;
    border-radius: 18px;
    padding: 8px 12px 4px 12px;
    box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.04);
  }}
  .callout-box {{
    position: absolute;
    right: 175px;
    top: 22px;
    background: #FFFFFF;
    border: 2px solid #0055FF;
    border-radius: 12px;
    padding: 8px 16px;
    box-shadow: 0 10px 25px -5px rgba(0, 85, 255, 0.18);
    z-index: 5;
  }}
  .callout-box::after {{
    content: "";
    position: absolute;
    bottom: -8px;
    right: 18px;
    width: 12px;
    height: 12px;
    background: #FFFFFF;
    border-right: 2px solid #0055FF;
    border-bottom: 2px solid #0055FF;
    transform: rotate(45deg);
  }}
  .callout-title {{
    font-size: 10.5px;
    font-weight: 800;
    letter-spacing: 0.8px;
    color: #0284C7;
    text-transform: uppercase;
  }}
  .callout-val {{
    font-size: 16px;
    font-weight: 900;
    color: #0F172A;
    margin-top: 1px;
  }}

  .progress-section {{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .progress-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }}
  .cap-title {{
    font-size: 18px;
    font-weight: 900;
    color: #0F172A;
  }}
  .cap-title span {{
    font-weight: 700;
    color: #475569;
    margin-left: 6px;
  }}
  .cap-pct {{
    font-size: 22px;
    font-weight: 900;
    color: #0055FF;
  }}
  .bar-bg {{
    width: 100%;
    height: 13px;
    background: #E2E8F0;
    border-radius: 999px;
    overflow: hidden;
  }}
  .bar-fill {{
    height: 100%;
    width: {pct:.2f}%;
    background: linear-gradient(90deg, #003896 0%, #0066FF 55%, #38BDF8 100%);
    border-radius: 999px;
    box-shadow: 0 0 12px rgba(0, 102, 255, 0.4);
  }}
  .progress-sub {{
    font-size: 14px;
    font-weight: 600;
    color: #475569;
  }}
  .progress-sub strong {{
    color: #0F172A;
    font-weight: 800;
  }}

  .footer {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13.5px;
    color: #475569;
    font-weight: 600;
    border-top: 1px solid rgba(203, 213, 225, 0.7);
    padding-top: 8px;
  }}
  .footer-handle {{
    font-weight: 800;
    color: #0F172A;
    font-size: 14.5px;
  }}
</style>
</head>
<body>
  <div class="top-banner">
    <div class="b-blue"></div>
    <div class="b-white"></div>
    <div class="b-red"></div>
  </div>

  <div class="container">
    <div class="top-row">
      <div class="eyebrow">ADHÉRENTS · {date_header}</div>
      <div class="pill-badge">
        <span class="pill-dot"></span>
        BAROMÈTRE D'ADHÉSION
      </div>
    </div>

    <div class="hero-block">
      <div class="total-val">{formater_nombre(adherents)}</div>
      <div class="total-sub">adhérents à Nouvelle Énergie</div>
    </div>

    <div class="kpi-row">
      <div class="kpi-card">
        <div class="kpi-val val-24h">+{formater_nombre(gain_24h)}</div>
        <div class="kpi-label">DERNIÈRES 24 H</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val val-7j">+{formater_nombre(gain_7j)}</div>
        <div class="kpi-label">SUR 7 JOURS</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val val-rate">{cadence_kpi}</div>
        <div class="kpi-label">ADHÉSIONS / HEURE</div>
      </div>
    </div>

    <div class="chart-wrapper">
      <div class="callout-box">
        <div class="callout-title">{badge_momentum}</div>
        <div class="callout-val">+{formater_nombre(gain_7j)} en 7 jours</div>
      </div>
      <canvas id="adhChart"></canvas>
    </div>

    <div class="progress-section">
      <div class="progress-header">
        <div class="cap-title">Cap <span>{formater_nombre(prochain_cap)}</span></div>
        <div class="cap-pct">{pct_int} %</div>
      </div>
      <div class="bar-bg">
        <div class="bar-fill"></div>
      </div>
      <div class="progress-sub">
        Tranche {formater_nombre(cap_precedent)} ➔ {formater_nombre(prochain_cap)} &nbsp;·&nbsp; Reste <strong>{formater_nombre(reste)}</strong> &nbsp;·&nbsp; Projection <strong>{eta_court}</strong>
      </div>
    </div>

    <div class="footer">
      <div>Données : unenouvelleenergie.fr · Évolution annuelle 2026</div>
      <div class="footer-handle">@CompteurNE</div>
    </div>
  </div>

  <script>
    const rawData = {json.dumps(chart_data)};
    const ctx = document.getElementById('adhChart').getContext('2d');

    const gradient = ctx.createLinearGradient(0, 0, 0, 210);
    gradient.addColorStop(0, 'rgba(0, 85, 255, 0.30)');
    gradient.addColorStop(0.75, 'rgba(0, 85, 255, 0.05)');
    gradient.addColorStop(1, 'rgba(0, 85, 255, 0.0)');

    // Plugin : Bandes de mois alternées, séparations verticales et nom des mois en filigrane
    const monthBandsPlugin = {{
      id: 'monthBandsPlugin',
      beforeDatasetsDraw(chart) {{
        const {{ ctx, chartArea: {{ top, bottom, left, right }}, scales: {{ x }} }} = chart;
        if (!x) return;

        const monthsShort = ['JANV.', 'FÉVR.', 'MARS', 'AVR.', 'MAI', 'JUIN', 'JUIL.', 'AOÛT', 'SEPT.', 'OCT.', 'NOV.', 'DÉC.'];
        const minTs = x.min;
        const maxTs = x.max;

        let curr = new Date(minTs);
        curr.setUTCDate(1);
        curr.setUTCHours(0, 0, 0, 0);

        ctx.save();
        let idx = 0;

        while (curr.getTime() <= maxTs) {{
          const mStart = curr.getTime();
          const nextMonth = new Date(Date.UTC(curr.getUTCFullYear(), curr.getUTCMonth() + 1, 1));
          const mEnd = nextMonth.getTime();

          const xStart = Math.max(left, x.getPixelForValue(mStart));
          const xEnd = Math.min(right, x.getPixelForValue(mEnd));
          const width = xEnd - xStart;

          if (width > 0) {{
            // Couleur de fond alternée par mois
            ctx.fillStyle = (idx % 2 === 0)
              ? 'rgba(226, 232, 240, 0.38)'
              : 'rgba(239, 246, 255, 0.65)';
            ctx.fillRect(xStart, top, width, bottom - top);

            // Ligne de séparation verticale au 1er du mois
            if (xStart > left + 4) {{
              ctx.beginPath();
              ctx.setLineDash([4, 4]);
              ctx.moveTo(xStart, top);
              ctx.lineTo(xStart, bottom);
              ctx.strokeStyle = 'rgba(148, 163, 184, 0.55)';
              ctx.lineWidth = 1.2;
              ctx.stroke();
              ctx.setLineDash([]);
            }}

            // Nom du mois en filigrane + repère en bas de colonne
            if (width > 34) {{
              const label = monthsShort[curr.getUTCMonth()];
              ctx.font = '900 18px Inter, sans-serif';
              ctx.fillStyle = 'rgba(100, 116, 139, 0.22)';
              ctx.textAlign = 'center';
              ctx.textBaseline = 'bottom';
              ctx.fillText(label, xStart + width / 2, bottom - 8);
            }}
          }}

          curr = nextMonth;
          idx++;
        }}
        ctx.restore();
      }}
    }};

    // Plugin : Double halo lumineux sur le point actuel
    const haloPlugin = {{
      id: 'haloPlugin',
      afterDatasetsDraw(chart) {{
        const meta = chart.getDatasetMeta(0);
        if (!meta.data.length) return;
        const lastPoint = meta.data[meta.data.length - 1];
        const ctx = chart.ctx;
        ctx.save();
        
        ctx.beginPath();
        ctx.arc(lastPoint.x, lastPoint.y, 18, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(237, 41, 57, 0.40)';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(lastPoint.x, lastPoint.y, 12, 0, 2 * Math.PI);
        ctx.fillStyle = 'rgba(237, 41, 57, 0.18)';
        ctx.fill();

        ctx.restore();
      }}
    }};

    new Chart(ctx, {{
      type: 'line',
      data: {{
        datasets: [{{
          data: rawData,
          borderColor: '#0055FF',
          borderWidth: 4.5,
          backgroundColor: gradient,
          fill: true,
          tension: 0.28,
          cubicInterpolationMode: 'monotone',
          pointRadius: (ctx) => {{
            const pt = ctx.raw;
            return (pt && pt.isLast) ? 7 : 0;
          }},
          pointBackgroundColor: '#ED2939',
          pointBorderColor: '#FFFFFF',
          pointBorderWidth: (ctx) => {{
            const pt = ctx.raw;
            return (pt && pt.isLast) ? 3 : 0;
          }}
        }}]
      }},
      plugins: [monthBandsPlugin, haloPlugin],
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: {{
          padding: {{ top: 16, right: 10, bottom: 2, left: 6 }}
        }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ enabled: false }}
        }},
        scales: {{
          x: {{
            type: 'time',
            time: {{
              unit: 'month'
            }},
            grid: {{ display: false }},
            border: {{ color: '#94A3B8', width: 1.5 }},
            ticks: {{
              color: '#1E293B',
              font: {{ family: 'Inter', size: 15, weight: '800' }},
              maxRotation: 0,
              autoSkip: false,
              callback: function(value) {{
                const d = new Date(value);
                return d.toLocaleDateString('fr-FR', {{ month: 'short' }}).toUpperCase();
              }}
            }}
          }},
          y: {{
            position: 'right',
            min: {min_y_axis},
            suggestedMax: {suggested_max_y},
            border: {{ display: false }},
            grid: {{
              color: 'rgba(148, 163, 184, 0.35)',
              lineWidth: 1.2
            }},
            ticks: {{
              color: '#1E293B',
              font: {{ family: 'Inter', size: 15.5, weight: '800' }},
              padding: 8,
              stepSize: 10000,
              callback: function(value) {{
                return (value / 1000) + 'k';
              }}
            }}
          }}
        }}
      }}
    }});
  </script>
</body>
</html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1200, "height": 675},
            device_scale_factor=2  # Qualité Retina (2400x1350 px)
        )
        page.set_content(html_content, wait_until="networkidle")
        page.screenshot(path=output_path)
        browser.close()

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

    adherents, gain_7j, date_maj, points_api = extraire_donnees()
    if adherents is None:
        print("Impossible d'extraire les données.", flush=True)
        sys.exit(1)

    maintenant = date_maj
    maintenant_iso = maintenant.isoformat()

    sauvegarder_csv(maintenant_iso, adherents, gain_7j)
    gain_24h = calculer_gain_24h(maintenant, adherents)

    cadence_txt, cadence_kpi = calculer_cadence_recente(CSV_FILE)
    momentum_txt, moyenne_jour_7j, badge_momentum = calculer_momentum(gain_24h, gain_7j)

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
            eta_court = f"~{int(eta_date.day)} {MOIS_FR[eta_date.month]}"
        else:
            eta_texte = "Indéterminée"
            eta_court = "—"

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

        generer_carte_visuelle(
            adherents=adherents,
            gain_24h=gain_24h,
            gain_7j=gain_7j,
            cadence_kpi=cadence_kpi,
            badge_momentum=badge_momentum,
            cap_precedent=cap_precedent,
            prochain_cap=prochain_cap,
            eta_court=eta_court,
            maintenant=maintenant,
            points_api=points_api,
            output_path=CHART_FILE,
        )

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
