import requests
import json
import re
import os

# 1. CORRECTION : La bonne URL du tableau de bord
URL_CIBLE = "https://adherent.unenouvelleenergie.fr/parrainer"

def recuperer_donnees():
    session = requests.Session()
    
    # 2. CORRECTION : Les headers exacts qui marchaient hier
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    # 3. CORRECTION : L'injection stricte du cookie avec son domaine
    ea_session = os.environ.get("EA_SESSION")
    if ea_session:
        session.cookies.set("ea_session", ea_session, domain="adherent.unenouvelleenergie.fr")
        
    resp = session.get(URL_CIBLE, headers=headers, timeout=15)
    resp.raise_for_status()
    
    # Vérification anti-redirection (si le cookie est mort)
    if "login" in resp.url or "connexion" in resp.url:
        raise ValueError("Redirection vers la page de connexion : Le cookie EA_SESSION a expiré.")

    # --- EXTRACTION ---
    # 1. On récupère le total avec la méthode Regex ultra-robuste
    match_collectif = re.search(r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text)
    if not match_collectif:
        raise ValueError("Impossible de trouver le total des adhérents sur la page.")
    total_adherents = int(match_collectif.group(1))

    # 2. On cible EXCLUSIVEMENT le tableau "departements"
    # Il commence par "departements":[ et se termine juste avant ,"seuilClassement"
    match_deps = re.search(r'\\?"departements\\?"\s*:\s*(\[.*?\])\s*,\s*\\?"seuilClassement\\?"', resp.text)

    if not match_deps:
        raise ValueError("Impossible d'isoler le tableau des départements dans le code source.")
        
    # On nettoie les guillemets d'échappement uniquement sur ce bloc précis
    deps_str = match_deps.group(1).replace('\\"', '"')

    try:
        departements = json.loads(deps_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Échec de la lecture JSON des départements : {e}\nExtrait : {deps_str[:100]}")
        
    return total_adherents, departements

if __name__ == "__main__":
    # Test d'exécution locale
    total, deps = recuperer_donnees()
    print(f"Succès : {total} adhérents et {len(deps)} départements trouvés.")