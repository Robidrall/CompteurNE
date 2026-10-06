import csv
import json
import os
from datetime import datetime

from extraction import recuperer_donnees
from visualisation import generer_graphique_departements

# --- CONFIGURATION ---
FICHIER_CSV = "data/adherents.csv"
FICHIER_JSON = "data/last_alert.json"
IMAGE_40K = "data/celebration_40k.png"
PALIER_CIBLE = 30000

def generer_message(total):
    """Génère un message différent selon l'heure de la journée."""
    heure_actuelle = datetime.now().hour
    
    if 5 <= heure_actuelle < 12:
         salutation = "Bonjour ! ☕ L'énergie monte dès le matin."
    elif 12 <= heure_actuelle < 18:
         salutation = "Bon après-midi ! ☀️ La dynamique se poursuit."
    else:
         salutation = "Bonsoir ! 🌙 Quelle mobilisation exceptionnelle aujourd'hui."
         
    texte = (
        f"{salutation}\n\n"
        f"Nous venons de franchir le cap historique des {total:,.0f} adhérents ! 🎉🇫🇷\n\n"
        f"Retrouvez la répartition de nos forces vives par département ci-dessous 👇\n\n"
        f"#NouvelleEnergie #DavidLisnard2027"
    )
    return texte

def main():
    try:
        # 1. Extraction (Appel au fichier extraction.py)
        total, departements = recuperer_donnees()
        print(f"Total relevé : {total} adhérents")
        
        # 2. Sauvegarde CSV
        os.makedirs('data', exist_ok=True)
        date_jour = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(FICHIER_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([date_jour, total])
            
        # 3. Gestion de l'événement des 40 000
        deja_fete_40k = False
        if os.path.exists(FICHIER_JSON):
             with open(FICHIER_JSON, 'r') as f:
                 last_data = json.load(f)
                 deja_fete_40k = last_data.get('cap_40k_fete', False)
                 
        if total >= PALIER_CIBLE and not deja_fete_40k:
            print(f"🚀 PALIER DES {PALIER_CIBLE} FRANCHI ! Génération de l'événement...")
            
            # Appel au fichier visualisation.py
            generer_graphique_departements(departements, IMAGE_40K)
            
            # Génération du message localisé dans le temps
            message = generer_message(total)
            
            print("\n--- Tweet prêt à publier ---")
            print(message)
            
            # TODO : Insérer ici la fonction d'envoi du Tweet (tweepy ou envoi mail)
            
            # Mise à jour de l'état pour éviter les doublons
            with open(FICHIER_JSON, 'w') as f:
                json.dump({'cap_40k_fete': True, 'date': date_jour}, f)
                
    except Exception as e:
        print(f"Erreur lors de l'exécution : {e}")
        # En cas d'automatisation, on lève l'erreur pour que GitHub Actions affiche du rouge
        raise

if __name__ == "__main__":
    main()