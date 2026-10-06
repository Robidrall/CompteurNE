import requests
import json
import re
import os

URL_CIBLE = "https://adherent.unenouvelleenergie.fr/"

def recuperer_donnees():
    """Extrait le JSON contenu dans la page et renvoie le total et les départements."""
    session = requests.Session()
    
    # Injection du cookie de session (important si la page est protégée)
    ea_session = os.environ.get("EA_SESSION")
    if ea_session:
        session.cookies.set("EA_SESSION", ea_session)
        
    try:
        response = session.get(URL_CIBLE)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Erreur de connexion à la cible : {e}")
    
    # 1. Recherche de la chaîne encodée
    match = re.search(r'self\.__next_f\.push\(\[1,"6:(.*?)"\]\]', response.text)
    if not match:
        raise ValueError("Bloc de données (Next.js) introuvable dans la page.")
    
    # 2. Décodage
    raw_json = match.group(1).replace('\\"', '"').replace('\\\\', '\\')
    
    # 3. Isolation de l'objet "d"
    json_str_match = re.search(r'\["\$","\$Lc",null,\{"d":(\{.*\})\}\]', raw_json)
    if not json_str_match:
         raise ValueError("Structure de l'objet 'd' introuvable.")
         
    donnees = json.loads(json_str_match.group(1))
    
    try:
        total_adherents = donnees['collectif']['adherents']
        departements = donnees['parrainage']['departements']
    except KeyError as e:
        raise KeyError(f"Clé manquante dans le JSON : {e}")
    
    return total_adherents, departements

if __name__ == "__main__":
    # Test simple lors de l'exécution directe de ce fichier
    total, deps = recuperer_donnees()
    print(f"Test d'extraction réussi : {total} adhérents trouvés.")