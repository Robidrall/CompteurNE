import requests
import json
import re
import os

URL_CIBLE = "https://adherent.unenouvelleenergie.fr/"

def recuperer_donnees():
    session = requests.Session()
    
    # 1. On se fait passer pour Google Chrome pour éviter le blocage anti-bot
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
    })
    
    ea_session = os.environ.get("EA_SESSION")
    if ea_session:
        session.cookies.set("EA_SESSION", ea_session)
        
    response = session.get(URL_CIBLE, allow_redirects=True)
    response.raise_for_status()
    
    # 2. Vérification stricte de la redirection
    if "login" in response.url or "connexion" in response.url:
        raise ValueError(f"🚨 REDIRECTION DÉTECTÉE vers {response.url} !\nLe cookie EA_SESSION est invalide ou le site utilise un autre nom de cookie (ex: next-auth.session-token).")

    # 3. Recherche des données (plus souple, sans dépendre du numéro de chunk Next.js)
    texte_propre = response.text.replace('\\"', '"').replace('\\\\', '\\')
    
    match = re.search(r'\["\$","\$Lc",null,\{"d":(\{.*?\})\}\]', texte_propre)
    if not match:
        raise ValueError("Le site a chargé, mais la structure des données (l'objet 'd') est introuvable. Le code source a pu être modifié par les développeurs.")
        
    donnees_brutes = match.group(1)
    
    # 4. Conversion et extraction
    donnees = json.loads(donnees_brutes)
    total_adherents = donnees['collectif']['adherents']
    departements = donnees['parrainage']['departements']
    
    return total_adherents, departements

if __name__ == "__main__":
    # Permet de tester le fichier tout seul
    total, deps = recuperer_donnees()
    print(f"Succès : {total} adhérents trouvés.")