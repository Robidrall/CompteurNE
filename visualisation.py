import pandas as pd
import plotly.express as px
import os

def generer_graphique_departements(departements, chemin_sortie="data/celebration_40k.png"):
    """Génère un graphique à barres moderne des 15 meilleurs départements."""
    
    # 1. Nettoyage : retirer les entrées invalides ou sans code
    deps_valides = [d for d in departements if d.get('code') and d.get('nom') and d.get('adherents')]
    
    if not deps_valides:
        raise ValueError("Aucune donnée départementale valide fournie pour le graphique.")

    df = pd.DataFrame(deps_valides)
    
    # 2. Trier et prendre le Top 15 (ordre inverse pour affichage Plotly de haut en bas)
    df_top15 = df.sort_values(by='adherents', ascending=False).head(15)
    df_top15 = df_top15.sort_values(by='adherents', ascending=True) 
    
    # 3. Création du visuel
    fig = px.bar(
        df_top15, 
        x='adherents', 
        y='nom', 
        orientation='h',
        text='adherents',
        title='<b>Top 15 des départements Nouvelle Énergie</b><br><sup>Cap historique des 40 000 adhérents franchi ! 🚀</sup>',
        labels={'nom': '', 'adherents': 'Nombre d\'adhérents'},
        color='adherents',
        color_continuous_scale='Blues'
    )
    
    # 4. Ajustements esthétiques
    fig.update_traces(textposition='outside', marker_line_color='rgb(8,48,107)', marker_line_width=1, opacity=0.9)
    fig.update_layout(
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Arial", size=14, color="#333333"),
        margin=dict(l=20, r=40, t=80, b=20),
        coloraxis_showscale=False
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#E5E5E5')
    fig.update_yaxes(tickfont=dict(size=14, weight='bold'))

    # 5. Export
    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)
    fig.write_image(chemin_sortie, width=1000, height=800, scale=2)
    print(f"Graphique généré avec succès : {chemin_sortie}")