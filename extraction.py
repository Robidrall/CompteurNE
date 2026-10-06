import requests
import json
import re
import os

URL_CIBLE = "https://adherent.unenouvelleenergie.fr/parrainer"

def recuperer_donnees():
    session = requests.Session()
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    ea_session = os.environ.get("EA_SESSION")
    if ea_session:
        session.cookies.set("ea_session", ea_session, domain="adherent.unenouvelleenergie.fr")
        
    resp = session.get(URL_CIBLE, headers=headers, timeout=15)
    resp.raise_for_status()
    
    if "login" in resp.url or "connexion" in resp.url:
        raise ValueError("Redirection vers la page de connexion : Le cookie EA_SESSION a expiré.")

    # 1. Total National
    match_collectif = re.search(r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text)
    if not match_collectif:
        raise ValueError("Impossible de trouver le total des adhérents sur la page.")
    total_adherents = int(match_collectif.group(1))
    
    # 2. Liste des Départements
    match_deps = re.search(r'\\?"departements\\?"\s*:\s*(\[.*?\])\s*,\s*\\?"seuilClassement\\?"', resp.text)
    if not match_deps:
        raise ValueError("Impossible d'isoler le tableau des départements dans le code source.")
        
    deps_str = match_deps.group(1).replace('\\"', '"')
    
    try:
        departements = json.loads(deps_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Échec de la lecture JSON des départements : {e}")
        
    return total_adherents, departements