"""Chargement de la grille d'évaluation fournie par le client.

Les questions du client **ne sont pas versionnées**. Elles sont lues à l'exécution
depuis `data/raw/`, qui est hors Git : le dépôt a vocation à devenir public, et diffuser
le support d'évaluation d'un client n'en fait pas partie. Ce module ne contient donc que
du code de chargement, aucune question.

Deux choix de conception, tous deux au service de la séparation posée par le lot 3 :

1. **Les questions de la grille gardent la taxonomie du client** (`Type de question`),
   préfixée `grille/`. Les ranger de force dans les propriétés dérivées du modèle de
   données demanderait un jugement par question — donc exactement le travail au cas par
   cas que le dispositif cherche à éviter. Les deux taxonomies cohabitent et s'agrègent
   séparément dans le rapport.

2. **Aucune assertion de valeur n'est déduite de la grille.** La colonne « Ce qu'on
   vérifie » décrit une attente en français, pas une valeur vérifiable. La grille est
   notée à la main, sur les trois colonnes que le client a lui-même définies.
"""

from __future__ import annotations

import pathlib

import pandas as pd

from tests.eval.assertions import SqlExecutable
from tests.eval.corpus import Cas

FICHIER = "grille_evaluation_agent_data.xlsx"

COLONNE_NUMERO = "#"
COLONNE_QUESTION = "Question"
COLONNE_TYPE = "Type de question"

# Les colonnes de notation définies par le client. Reproduites telles quelles dans le
# rapport : c'est sur elles que la recette se fera, pas sur nos propres critères.
COLONNES_JUGEMENT = (
    "SQL Check\n(Valide / Invalide)",
    "Chart Check\n(Adapté / Illisible)",
    "Pédagogie IA\n(Réglo / Dangereux)",
)


def chemin_par_defaut(racine: pathlib.Path) -> pathlib.Path:
    return racine / "data" / "raw" / FICHIER


def charger(chemin: pathlib.Path) -> tuple[Cas, ...]:
    """Lit la grille et en fait des cas d'évaluation.

    Renvoie un tuple vide si le fichier est absent : le harnais doit rester exécutable
    sans les données client, sinon il n'est pas reprenable.
    """
    if not chemin.exists():
        return ()

    df = pd.read_excel(chemin)
    # Les deux colonnes sont exigées. Sans le numéro, le filtre des lignes de service
    # écarterait tout et le chargeur renverrait zéro question en silence — un harnais
    # vide qui ne se plaint pas est pire qu'un harnais qui refuse de démarrer.
    manquantes = [c for c in (COLONNE_NUMERO, COLONNE_QUESTION) if c not in df.columns]
    if manquantes:
        raise ValueError(
            f"colonne(s) {manquantes} absente(s) de {chemin.name} — "
            f"colonnes trouvées : {list(df.columns)}"
        )

    cas = []
    for _, ligne in df.iterrows():
        # Une question est une ligne **numérotée**. Le tableur contient aussi des
        # lignes de service : une consigne de remplissage peut occuper la colonne
        # Question, et un filtre sur le seul vide la laisserait passer. On poserait
        # alors une consigne à l'agent, et le score s'en trouverait faussé sans que
        # rien ne le signale.
        if pd.isna(ligne.get(COLONNE_NUMERO)):
            continue
        question = str(ligne[COLONNE_QUESTION]).strip()
        if not question or question.lower() == "nan":
            continue
        # `str()` d'une cellule vide de pandas donne "nan", pas "" : sans ce filtre,
        # une ligne non typée produirait une propriété nommée « grille/nan ».
        brut = ligne.get(COLONNE_TYPE)
        type_client = "non typée" if pd.isna(brut) else str(brut).strip() or "non typée"
        cas.append(
            Cas(
                propriete=f"grille/{type_client}",
                question=question,
                # Seul l'exécutabilité est automatisable ici. Le reste relève du
                # jugement, sur les colonnes du client.
                assertions=(SqlExecutable(),),
                source="grille",
                note=str(ligne.get("Ce qu'on vérifie", "")).strip(),
            )
        )
    return tuple(cas)


def grille_de_notation(cas: tuple[Cas, ...]) -> str:
    """Tableau vierge à remplir à la main, aux colonnes du client."""
    de_la_grille = [c for c in cas if c.source == "grille"]
    if not de_la_grille:
        return ""

    entetes = ["Question"] + [c.split("\n")[0] for c in COLONNES_JUGEMENT]
    lignes = [
        "## Notation manuelle (grille client)",
        "",
        "| " + " | ".join(entetes) + " |",
        "|" + "---|" * len(entetes),
    ]
    for c in de_la_grille:
        court = c.question if len(c.question) <= 90 else c.question[:87] + "…"
        lignes.append("| " + court + " |" + " |" * len(COLONNES_JUGEMENT))
    return "\n".join(lignes)
