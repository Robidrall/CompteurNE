import csv
from datetime import datetime, timezone
from email.mime.text import MIMEText
import json
import os
import re
import smtplib
import sys
import requests

print(">>> [1/5] Demarrage du script Python...", flush=True)

COOKIE = os.getenv("EA_SESSION")
SMTP_USER = os.getenv("GMAIL_USER")
SMTP_PASS = os.getenv("GMAIL_APP_PASS")

print(f">>> [2/5] Verification des variables d'environnement :", flush=True)
print(f"    - EA_SESSION present : {bool(COOKIE)}", flush=True)
print(f"    - GMAIL_USER present : {bool(SMTP_USER)}", flush=True)
print(f"    - GMAIL_APP_PASS present : {bool(SMTP_PASS)}", flush=True)

if not COOKIE:
    print("ERREUR FATALE : Cookie EA_SESSION manquant dans l'environnement.", flush=True)
    sys.exit(1)

URL = "https://adherent.unenouvelleenergie.fr/parrainer"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print(">>> [3/5] Requete vers le site...", flush=True)
try:
    session = requests.Session()
    session.cookies.set("ea_session", COOKIE, domain="adherent.unenouvelleenergie.fr")
    resp = session.get(URL, headers=headers, timeout=15)
    print(f"    - Code HTTP : {resp.status_code}", flush=True)
except Exception as e:
    print(f"ERREUR REQUETE : {e}", flush=True)
    sys.exit(1)

match_collectif = re.search(r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text)
match_gain = re.search(r'\\?"gainSemaine\\?"\s*:\s*(\d+)', resp.text)

if not match_collectif:
    print("ERREUR EXTRACTION : Impossible de trouver le bloc collectif dans la page.", flush=True)
    print(f"Apercu reponse (200 premiers car.) : {resp.text[:200]}", flush=True)
    sys.exit(1)

adherents = int(match_collectif.group(1))
gain_7j = int(match_gain.group(1)) if match_gain else 0
print(f">>> [4/5] Donnees extraites : {adherents} adherents (+{gain_7j} sur 7j)", flush=True)

# Test d'envoi du mail
print(f">>> [5/5] Tentative d'envoi du mail a {SMTP_USER}...", flush=True)
try:
    corps = f"Point compteur : {adherents} adherents Nouvelle Energie."
    msg = MIMEText(corps, "plain", "utf-8")
    msg["Subject"] = f"Test Bot NE : {adherents} adherents"
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [SMTP_USER], msg.as_string())
    print("SUCCES : Le mail a ete envoye !", flush=True)
except Exception as e:
    print(f"ERREUR ENVOI MAIL : {e}", flush=True)
    sys.exit(1)