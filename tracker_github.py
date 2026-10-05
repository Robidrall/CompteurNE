import csv
from datetime import datetime, timezone
from email.mime.text import MIMEText
import json
import os
import re
import smtplib
import requests

# Seuils de notification
SEUIL_ADHERENTS = 25  # Alerte si +Y adhérents
SEUIL_HEURES = 12  # Ou récapitulatif toutes les X heures
OBJECTIF = 60000

URL = "https://adherent.unenouvelleenergie.fr/parrainer"
CSV_FILE = "data/adherents.csv"
STATE_FILE = "data/last_alert.json"

COOKIE = os.getenv("EA_SESSION")
SMTP_USER = os.getenv("GMAIL_USER")  # Ton adresse gmail
SMTP_PASS = os.getenv("GMAIL_APP_PASS")  # Ton mot de passe d'application
DESTINATAIRE = os.getenv(
    "EMAIL_DESTINATAIRE", SMTP_USER
)  # Par défaut, envoyé à toi-même


def extraire_donnees():
  session = requests.Session()
  session.cookies.set(
      "ea_session", COOKIE, domain="adherent.unenouvelleenergie.fr"
  )
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      ),
      "Accept": (
          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
      ),
  }
  resp = session.get(URL, headers=headers, timeout=15)
  resp.raise_for_status()

  match_collectif = re.search(
      r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text
  )
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


def lire_etat():
  if os.path.isfile(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
      return json.load(f)
  return None


def ecrire_etat(iso_date, adherents):
  os.makedirs("data", exist_ok=True)
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"timestamp": iso_date, "adherents": adherents}, f, indent=2)


def envoyer_email(sujet, contenu):
  if not SMTP_USER or not SMTP_PASS:
    print("Identifiants e-mail non configurés.")
    return

  msg = MIMEText(contenu, "plain", "utf-8")
  msg["Subject"] = sujet
  msg["From"] = SMTP_USER
  msg["To"] = DESTINATAIRE

  with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(SMTP_USER, [DESTINATAIRE], msg.as_string())
  print(f"E-mail envoyé avec succès à {DESTINATAIRE}")


def run():
  if not COOKIE:
    print("Erreur : EA_SESSION manquant.")
    return

  adherents, gain_7j = extraire_donnees()
  if adherents is None:
    print("Impossible de lire les chiffres. Le cookie a peut-être expiré.")
    return

  maintenant = datetime.now(timezone.utc)
  maintenant_iso = maintenant.isoformat()

  # Enregistrement dans le CSV
  sauvegarder_csv(maintenant_iso, adherents, gain_7j)
  print(f"Relevé archivé : {adherents} adhérents")

  # Vérification des seuils
  etat = lire_etat()
  if etat is None:
    ecrire_etat(maintenant_iso, adherents)
    print("État initialisé.")
    #return

  dernier_total = etat["adherents"]
  derniere_date = datetime.fromisoformat(etat["timestamp"])

  delta_adherents = adherents - dernier_total
  delta_heures = (maintenant - derniere_date).total_seconds() / 3600

  doit_notifier = False
  type_alerte = ""

  if delta_adherents >= SEUIL_ADHERENTS:
    doit_notifier = True
    type_alerte = f"⚡ Palier franchi : {adherents:,} adhérents !".replace(
        ",", " "
    )
  elif delta_heures >= SEUIL_HEURES:
    doit_notifier = True
    type_alerte = (
        f"📊 Point d'étape quotidien : {adherents:,} adhérents".replace(
            ",", " "
        )
    )

  if doit_notifier:
    pourcentage = (adherents / OBJECTIF) * 100

    # Texte prêt à être copié-collé sur X
    tweet_texte = (
        f"Point d'étape · @nouv_energie\n\n"
        f"👥 {adherents:,} adhérents\n"
        f"🎯 {pourcentage:.1f} % de l'objectif ({OBJECTIF:,})\n"
        f"📈 +{gain_7j:,} sur 7 jours (+{delta_adherents} depuis le dernier"
        " point)\n\n"
        f"#NouvelleEnergie #DavidLisnard"
    ).replace(",", " ")

    email_corps = (
        f"Bonjour,\n\n"
        f"Un nouveau point d'étape est disponible.\n\n"
        f"--- TEXTE À COPIER / COLLER POUR LE TWEET ---\n\n"
        f"{tweet_texte}\n\n"
        f"----------------------------------------------\n"
        f"Lien direct pour tweeter : https://twitter.com/intent/tweet?text="
        f"{requests.utils.quote(tweet_texte)}"
    )

    envoyer_email(type_alerte, email_corps)
    ecrire_etat(maintenant_iso, adherents)
  else:
    print(
        f"Pas d'alerte (+{delta_adherents}/{SEUIL_ADHERENTS} adh,"
        f" {delta_heures:.1f}/{SEUIL_HEURES}h)."
    )


if __name__ == "__main__":
  run()