import pandas as pd
import plotly.express as px
import os

def generer_graphique_departements(departements, chemin_sortie="data/top15_departements.png"):
    """Génère un graphique à barres du Top 15 des départements."""
    
    # Filtrer les données valides
    deps_valides = [d for d in departements if d.get('code') and d.get('nom') and d.get('adherents')]
    
    if not deps_valides:
        print("Aucune donnée départementale valide.")
        return None

    df = pd.DataFrame(deps_valides)
    
    # Trier par nombre d'adhérents décroissant et prendre les 15 premiers
    df_top15 = df.sort_values(by='adherents', ascending=False).head(15)
    
    # Inverser l'ordre pour que le n°1 soit en haut du graphique horizontal
    df_top15 = df_top15.sort_values(by='adherents', ascending=True) 
    
    # Créer une étiquette combinée "Nom (Code)"
    df_top15['libelle'] = df_top15['nom'] + ' (' + df_top15['code'].astype(str) + ')'

    # Création du graphique avec Plotly
    fig = px.bar(
        df_top15, 
        x='adherents', 
        y='libelle', 
        orientation='h',
        text='adherents',
        title='<b>Top 15 des départements - Nouvelle Énergie</b>',
        labels={'libelle': '', 'adherents': 'Nombre d\'adhérents'},
        color='adherents',
        color_continuous_scale='Blues' # Dégradé élégant
    )
    
    # Esthétique générale
    fig.update_traces(
        textposition='outside', 
        marker_line_color='rgb(8,48,107)', 
        marker_line_width=1.5, 
        opacity=0.9
    )
    fig.update_layout(
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Arial", size=14, color="#333333"),
        margin=dict(l=20, r=60, t=80, b=20),
        coloraxis_showscale=False,
        title_font_size=20
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#E5E5E5')
    fig.update_yaxes(tickfont=dict(size=13, weight='bold'))

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)
    fig.write_image(chemin_sortie, width=900, height=700, scale=2)
    print(f"Graphique généré : {chemin_sortie}")
    return chemin_sortie