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
    # 1. On récupère le total avec ta méthode Regex ultra-robuste
    match_collectif = re.search(r'\\?"collectif\\?"\s*:\s*\{\s*\\?"adherents\\?"\s*:\s*(\d+)', resp.text)
    if not match_collectif:
        raise ValueError("Impossible de trouver le total des adhérents sur la page.")
    total_adherents = int(match_collectif.group(1))
    
    # 2. On récupère le bloc JSON complet pour isoler les départements
    texte_propre = resp.text.replace('\\"', '"').replace('\\\\', '\\')
    match_json = re.search(r'\["\$","\$Lc",null,\{"d":(\{.*?\})\}\]', texte_propre)
    
    if match_json:
        donnees = json.loads(match_json.group(1))
        departements = donnees.get('parrainage', {}).get('departements', [])
    else:
        raise ValueError("Impossible de trouver le détail des départements dans le code source.")
        
    return total_adherents, departements

if __name__ == "__main__":
    # Test d'exécution locale
    total, deps = recuperer_donnees()
    print(f"Succès : {total} adhérents et {len(deps)} départements trouvés.")