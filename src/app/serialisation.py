"""Traduction des types du noyau vers du JSON.

Un module à part, et pas trois lignes dans la route. Ce qu'il y a à traduire n'est pas
évident : DuckDB rend des `Decimal` dès qu'une colonne est DECIMAL — ce que produit tout
`SUM` sur des montants — des `date` et des `datetime` sur les colonnes temporelles, et des
tuples pour les lignes. Aucun de ces quatre types n'est sérialisable en JSON. Un oubli ne
donne pas un chiffre faux : il donne une erreur 500 en pleine démonstration.

**Le `Decimal` devient un flottant, pas une chaîne.** C'est un arbitrage : la chaîne
conserverait la précision exacte, le flottant permet à l'interface de calculer et de
dessiner. Comme la vocation de ces valeurs est d'être tracées, le flottant gagne. La
précision perdue est très en deçà de ce qu'un graphique ou un montant affiché distingue.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from src import charts
from src.agent.reponse import AgentResponse
from src.app import schemas


def cellule(valeur: Any) -> Any:
    """Une valeur de résultat, rendue transportable.

    L'ordre des tests n'est pas indifférent : `bool` est un `int` en Python et `datetime`
    est une `date`, donc les deux cas les plus spécifiques passent en premier. Inverser
    donnerait `True` sérialisé en `1`, et un horodatage tronqué à sa date.

    Le repli sur `str()` est **délibéré**. Un type exotique — extension DuckDB, structure
    imbriquée — ne doit pas faire tomber la réponse entière : l'interface affichera une
    représentation textuelle, ce qui est dégradé mais lisible. Un plantage au milieu d'une
    démonstration coûte plus cher qu'une cellule mal formatée.
    """
    if valeur is None or isinstance(valeur, (bool, int, float, str)):
        return valeur
    if isinstance(valeur, decimal.Decimal):
        return float(valeur)
    if isinstance(valeur, (datetime.datetime, datetime.date, datetime.time)):
        return valeur.isoformat()
    if isinstance(valeur, datetime.timedelta):
        return valeur.total_seconds()
    if isinstance(valeur, uuid.UUID):
        return str(valeur)
    if isinstance(valeur, (bytes, bytearray)):
        return valeur.hex()
    if isinstance(valeur, (list, tuple)):
        return [cellule(v) for v in valeur]
    if isinstance(valeur, dict):
        return {str(c): cellule(v) for c, v in valeur.items()}
    return str(valeur)


def requete(executee) -> schemas.RequeteSortante:
    """Chaque requête réussie porte la spécification que son résultat *permettrait*.

    Calculée ici par les mêmes règles pures que le graphique principal — aucun appel,
    aucun état — pour que l'interface puisse proposer « tracer » sur un bloc de requête
    sans redéployer de logique de lisibilité côté navigateur.
    """
    tracable = (
        charts.proposer(executee.colonnes, executee.lignes)
        if executee.a_reussi
        else None
    )
    return schemas.RequeteSortante(
        sql=executee.sql,
        colonnes=list(executee.colonnes),
        lignes=[[cellule(v) for v in ligne] for ligne in executee.lignes],
        tronque=executee.tronque,
        duree_ms=executee.duree_ms,
        erreur=executee.erreur,
        graphique=graphique(tracable),
    )


def graphique(spec) -> schemas.GraphiqueSortant | None:
    """Les étiquettes passent par `cellule()`, les valeurs sont déjà des flottants.

    L'abscisse peut porter des dates, que `src/charts` garde en objets Python — il
    travaille sur les types du noyau et ignore qu'une frontière HTTP existe. C'est ici,
    et seulement ici, qu'elles deviennent transportables.
    """
    if spec is None:
        return None
    return schemas.GraphiqueSortant(
        type=spec.type,
        x=spec.x,
        etiquettes=[cellule(e) for e in spec.etiquettes],
        series=[
            schemas.SerieSortante(
                colonne=s.colonne,
                valeurs=list(s.valeurs),
                axe_secondaire=s.axe_secondaire,
            )
            for s in spec.series
        ],
        variantes=list(spec.variantes),
        empilable=spec.empilable,
    )


def reponse(agent_response: AgentResponse) -> schemas.ReponseSortante:
    """`arret_normal` est calculé ici, jamais réinterprété par l'interface.

    `Arret.est_normal` porte déjà la distinction — notamment le fait qu'une réponse sans
    aucune requête est un succès, parce que le prompt invite à demander une précision sur
    une question ambiguë. La rejouer côté navigateur la ferait diverger un jour, et un
    abandon finirait par s'afficher comme une réponse aboutie.
    """
    return schemas.ReponseSortante(
        texte=agent_response.texte,
        requetes=[requete(r) for r in agent_response.requetes],
        graphique=graphique(agent_response.graphique),
        arret=agent_response.arret.value,
        arret_normal=agent_response.arret.est_normal,
        usage=schemas.UsageSortant(
            entree=agent_response.usage.entree,
            sortie=agent_response.usage.sortie,
            cache_lu=agent_response.usage.cache_lu,
            cache_ecrit=agent_response.usage.cache_ecrit,
        ),
        modele=agent_response.modele,
    )
