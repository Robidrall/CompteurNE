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

# Heure de Paris (UTC+2 en été, UTC+1 en hiver)
TZ_FR = timezone(timedelta(hours=2))

def calculer_cadence_recente(csv_file: str = CSV_FILE) -> str:
    if not os.path.isfile(csv_file):
        return "Données insuffisantes"
    with open(csv_file, "r", encoding="utf-8") as f:
        lignes = [r for r in csv.reader(f) if r]
    if len(lignes) < 3:
        return "Historique en cours de constitution"

    row_prec, row_dernier = lignes[-2], lignes[-1]
    try:
        dt_prec = datetime.fromisoformat(row_prec[0]).astimezone(TZ_FR)
        dt_dernier = datetime.fromisoformat(row_dernier[0]).astimezone(TZ_FR)
        adh_prec, adh_dernier = int(row_prec[1]), int(row_dernier[1])
    except (ValueError, IndexError):
        return "Format invalide"

    delta_sec = (dt_dernier - dt_prec).total_seconds()
    delta_heures = delta_sec / 3600
    delta_minutes = int(round(delta_sec / 60))
    delta_adh = adh_dernier - adh_prec

    # Heures locales françaises
    h_debut = dt_prec.strftime("%Hh%M")
    h_fin = dt_dernier.strftime("%Hh%M")

    duree_txt = f"{delta_minutes} min" if delta_minutes < 60 else f"{int(delta_heures)}h{int(delta_minutes % 60):02d}"

    # Formulation claire de la fenêtre observée
    contexte = f"de {h_debut} à {h_fin} ({duree_txt})"

    if delta_heures <= 0 or delta_adh <= 0:
        return f"Rythme calme {contexte}"

    adh_par_heure = delta_adh / delta_heures
    if adh_par_heure >= 10:
        val_txt = f"~{int(round(adh_par_heure))} adh. / h"
    elif adh_par_heure >= 1.0:
        val_txt = f"~{adh_par_heure:.1f} adh. / h"
    else:
        val_txt = f"1 adh. toutes les {delta_heures / delta_adh:.1f} h"

    return f"{val_txt} · {contexte}"

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

        gain_7j = 0
        if jours:
            idx_7j = -7 if len(jours) >= 7 else 0
            gain_7j = adherents - jours[idx_7j].get("adherents", adherents)

        return adherents, gain_7j, date_maj

    except requests.exceptions.RequestException as e:
        print(f"Erreur API : {e}")
        return None, None, None

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
    cap_precedent: int,
    prochain_cap: int,
    output_path: str,
):
    """Génère la carte visuelle HTML/CSS + Chart.js avec @CompteurNE dans l'en-tête."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    points = list(JALONS_HISTORIQUES)
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

    jalons_ts = {int(j[0].timestamp() * 1000) for j in JALONS_HISTORIQUES}
    
    chart_data = []
    sorted_ts = sorted(unique_points.keys())
    for i, ts in enumerate(sorted_ts):
        chart_data.append({
            "x": ts,
            "y": unique_points[ts],
            "isMilestone": ts in jalons_ts,
            "isLast": (i == len(sorted_ts) - 1)
        })

    gain_tranche = adherents - cap_precedent
    total_tranche = prochain_cap - cap_precedent
    pct = min(100.0, max(0.0, (gain_tranche / total_tranche) * 100))
    pct_int = int(pct)
    reste = prochain_cap - adherents

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
    height: 10px;
    width: 100%;
    display: flex;
  }}
  .top-banner div {{ flex: 1; }}
  .b-blue {{ background: #002654; }}
  .b-white {{ background: #FFFFFF; }}
  .b-red {{ background: #ED2939; }}

  .container {{
    padding: 34px 56px 26px 56px;
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}

  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}

  .brand-title {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }}

  .flag-icon {{
    width: 28px;
    height: 20px;
    display: flex;
    border-radius: 4px;
    overflow: hidden;
    box-shadow: 0 2px 5px rgba(15, 23, 42, 0.12);
    border: 1px solid #CBD5E1;
  }}
  .flag-icon div {{ flex: 1; }}

  .title-main {{
    font-size: 18px;
    font-weight: 900;
    letter-spacing: 0.6px;
    color: #0F172A;
  }}
  .title-sub {{
    font-size: 17px;
    font-weight: 700;
    color: #64748B;
  }}

  .hero-number {{
    display: flex;
    align-items: baseline;
    gap: 16px;
  }}
  .total-val {{
    font-size: 76px;
    font-weight: 900;
    letter-spacing: -2.5px;
    line-height: 1;
    background: linear-gradient(95deg, #0F172A 0%, #1E3A8A 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .total-unit {{
    font-size: 30px;
    font-weight: 800;
    color: #0284C7;
  }}

  .right-header {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 14px;
  }}

  .handle-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #0F172A;
    color: #FFFFFF;
    padding: 7px 16px;
    border-radius: 999px;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.3px;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.15);
  }}

  .kpi-group {{
    display: flex;
    gap: 14px;
  }}
  .kpi-card {{
    background: rgba(255, 255, 255, 0.9);
    border: 1px solid #E2E8F0;
    border-radius: 16px;
    padding: 14px 22px;
    min-width: 160px;
    box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.05);
  }}
  .kpi-label {{
    font-size: 11px;
    font-weight: 800;
    color: #64748B;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }}
  .kpi-val {{
    font-size: 28px;
    font-weight: 900;
    letter-spacing: -0.8px;
  }}
  .val-24h {{ color: #059669; }}
  .val-7j {{ color: #0284C7; }}

  .progress-box {{
    background: rgba(255, 255, 255, 0.85);
    border: 1px solid #E2E8F0;
    border-radius: 16px;
    padding: 16px 24px;
    box-shadow: 0 4px 15px rgba(15, 23, 42, 0.03);
  }}
  .progress-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 10px;
  }}
  .cap-title {{
    font-size: 16px;
    font-weight: 800;
    color: #1E293B;
  }}
  .cap-title span {{
    font-weight: 600;
    color: #64748B;
    font-size: 14px;
    margin-left: 6px;
  }}
  .cap-stats {{
    font-size: 14px;
    font-weight: 600;
    color: #475569;
  }}
  .cap-stats strong {{
    color: #0055FF;
    font-weight: 800;
  }}
  .bar-bg {{
    width: 100%;
    height: 14px;
    background: #E2E8F0;
    border-radius: 999px;
    overflow: hidden;
    padding: 2px;
  }}
  .bar-fill {{
    height: 100%;
    width: {pct:.2f}%;
    background: linear-gradient(90deg, #003896 0%, #0066FF 55%, #38BDF8 100%);
    border-radius: 999px;
    box-shadow: 0 0 12px rgba(0, 102, 255, 0.45);
  }}

  .chart-card {{
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 20px;
    padding: 20px 24px 14px 20px;
    height: 315px;
    box-shadow: 0 12px 30px -8px rgba(15, 23, 42, 0.06);
    position: relative;
  }}

  .footer {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    color: #64748B;
    font-weight: 600;
    padding-top: 2px;
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
    <div class="header">
      <div>
        <div class="brand-title">
          <div class="flag-icon">
            <div class="b-blue"></div>
            <div class="b-white"></div>
            <div class="b-red"></div>
          </div>
          <span class="title-main">BAROMÈTRE D'ADHÉSION</span>
        </div>
        <div class="hero-number">
          <span class="total-val">{formater_nombre(adherents)}</span>
          <span class="total-unit">adhérents</span>
        </div>
      </div>

      <div class="right-header">
        <div class="handle-badge">@CompteurNE</div>
        <div class="kpi-group">
          <div class="kpi-card">
            <div class="kpi-label">Dernières 24h</div>
            <div class="kpi-val val-24h">+{formater_nombre(gain_24h)}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Sur 7 jours</div>
            <div class="kpi-val val-7j">+{formater_nombre(gain_7j)}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="progress-box">
      <div class="progress-top">
        <div class="cap-title">
          Cap {prochain_cap // 1000}k <span>(tranche {cap_precedent // 1000}k ➔ {prochain_cap // 1000}k)</span>
        </div>
        <div class="cap-stats">
          <strong>{pct_int} % atteint</strong> (+{formater_nombre(gain_tranche)} / {formater_nombre(total_tranche)}) &nbsp;·&nbsp; Reste <strong>{formater_nombre(reste)}</strong> adhésions
        </div>
      </div>
      <div class="bar-bg">
        <div class="bar-fill"></div>
      </div>
    </div>

    <div class="chart-card">
      <canvas id="adhChart"></canvas>
    </div>

    <div class="footer">
      <div>Données · Source : unenouvelleenergie.fr</div>
      <div>Actualisé automatiquement</div>
    </div>
  </div>

  <script>
    const rawData = {json.dumps(chart_data)};
    const ctx = document.getElementById('adhChart').getContext('2d');

    const gradient = ctx.createLinearGradient(0, 0, 0, 275);
    gradient.addColorStop(0, 'rgba(0, 102, 255, 0.26)');
    gradient.addColorStop(0.75, 'rgba(0, 102, 255, 0.04)');
    gradient.addColorStop(1, 'rgba(0, 102, 255, 0.0)');

    new Chart(ctx, {{
      type: 'line',
      data: {{
        datasets: [{{
          data: rawData,
          borderColor: '#0055FF',
          borderWidth: 4,
          backgroundColor: gradient,
          fill: true,
          tension: 0.35,
          cubicInterpolationMode: 'monotone',
          pointRadius: (ctx) => {{
            const pt = ctx.raw;
            if (!pt) return 0;
            if (pt.isLast) return 8;
            if (pt.isMilestone) return 5.5;
            return 0;
          }},
          pointBackgroundColor: (ctx) => {{
            const pt = ctx.raw;
            if (!pt) return '#0055FF';
            if (pt.isLast) return '#ED2939';
            if (pt.isMilestone) return '#002654';
            return '#0055FF';
          }},
          pointBorderColor: '#FFFFFF',
          pointBorderWidth: (ctx) => {{
            const pt = ctx.raw;
            return (pt && (pt.isLast || pt.isMilestone)) ? 2.5 : 0;
          }}
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: {{
          padding: {{ top: 10, right: 16, bottom: 2, left: 4 }}
        }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ enabled: false }}
        }},
        scales: {{
          x: {{
            type: 'time',
            time: {{
              unit: 'day',
              displayFormats: {{ day: 'dd MMM' }}
            }},
            grid: {{ display: false }},
            border: {{ color: '#E2E8F0' }},
            ticks: {{
              color: '#64748B',
              font: {{ family: 'Inter', size: 12, weight: '600' }},
              maxTicksLimit: 9,
              callback: function(value) {{
                const d = new Date(value);
                return d.toLocaleDateString('fr-FR', {{ day: '2-digit', month: 'short' }});
              }}
            }}
          }},
          y: {{
            min: 24000,
            suggestedMax: {max(unique_points.values()) + 1200},
            border: {{ display: false, dash: [5, 5] }},
            grid: {{
              color: '#F1F5F9',
              lineWidth: 1.5
            }},
            ticks: {{
              color: '#64748B',
              font: {{ family: 'Inter', size: 12, weight: '600' }},
              callback: function(value) {{
                return value.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, " ");
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
            device_scale_factor=2
        )
        page.set_content(html_content, wait_until="networkidle")
        page.screenshot(path=output_path)
        browser.close()

    # 4. Capture HD via Playwright
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

    adherents, gain_7j, date_maj = extraire_donnees()
    if adherents is None:
        print("Impossible d'extraire les données.", flush=True)
        sys.exit(1)

    maintenant = date_maj
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
