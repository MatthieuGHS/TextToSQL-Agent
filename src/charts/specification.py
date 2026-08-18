"""Décider s'il y a un graphique à faire, et lequel.

Fonctions pures, à la discipline de `etl/transforms.py` : aucun fichier, aucune connexion,
aucun log. Ce module reçoit la forme d'un résultat de requête et rend une spécification —
il ne dessine rien, et ne sait pas qu'une interface existe.

**Pourquoi le code décide, et non le modèle.** Les règles en jeu sont des propriétés des
données, pas des questions de goût : deux unités ne se superposent pas, un NULL n'est pas
un zéro, un résultat vide ne se trace pas. `principes.md` les énonçait déjà, mais dans un
texte que rien n'applique — et un texte se contourne. Elles descendent ici, où elles sont
vérifiables.

Le SQL que le modèle écrit **est** l'expression de son intention : les colonnes qu'il
sélectionne sont celles qu'il veut montrer. On lit donc la forme du résultat plutôt que de
demander au modèle de la décrire une seconde fois.

**Le refus est un résultat de premier ordre**, pas un cas d'erreur. Une bonne part des
questions du corpus sont des pièges, et « pas de graphique, et voici pourquoi » y est la
bonne réponse.
"""

from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

# Au-delà, un axe catégoriel devient une bouillie d'étiquettes illisible. Le seuil vaut
# pour les barres seulement : une courbe temporelle de 300 points reste lisible.
MAX_CATEGORIES = 30

# Au-delà, aucune palette ne distingue plus les séries les unes des autres.
MAX_SERIES = 4

# Rapport entre les médianes de deux séries au-delà duquel elles ne partagent plus un axe.
# Calibré sur les ordres de grandeur du jeu de données : un coût hebdomadaire et un nombre
# de compteurs se lisent ensemble, des GRP et des clics non. À revoir sur mesure, pas
# d'intuition.
FACTEUR_SECOND_AXE = 25

COURBE = "courbe"
BARRES = "barres"

_TEMPORELS = (datetime.date, datetime.datetime)


@dataclass(frozen=True)
class Serie:
    """Une colonne numérique à tracer.

    `valeurs` garde les `None` : un trou dans une série est une information — `cost` est
    NULL sur tout le SEO, non acheté et non gratuit. Les remplacer par zéro raconterait
    une chute qui n'a pas eu lieu, et c'est le genre d'erreur qu'un graphique rend
    convaincante.
    """

    colonne: str
    valeurs: tuple[float | None, ...]
    axe_secondaire: bool = False


@dataclass(frozen=True)
class Graphique:
    type: str
    x: str
    etiquettes: tuple[Any, ...]
    series: tuple[Serie, ...]


def _est_numerique(v: Any) -> bool:
    # `bool` est un `int` en Python : un booléen n'est pas une mesure à tracer.
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _colonne_numerique(lignes: Sequence[Sequence[Any]], i: int) -> bool:
    """Numérique si toutes ses valeurs renseignées le sont, et qu'il en reste une."""
    valeurs = [l[i] for l in lignes if l[i] is not None]
    return bool(valeurs) and all(_est_numerique(v) for v in valeurs)


def _est_temporelle(lignes: Sequence[Sequence[Any]], i: int) -> bool:
    valeurs = [l[i] for l in lignes if l[i] is not None]
    return bool(valeurs) and all(isinstance(v, _TEMPORELS) for v in valeurs)


def _est_ordinale(lignes: Sequence[Sequence[Any]], i: int) -> bool:
    """Une colonne numérique qui sert d'abscisse plutôt que de mesure.

    Le cas visé est réel et fréquent : `SELECT EXTRACT(year FROM step_date), SUM(cost)`
    rend deux colonnes numériques, dont la première est un axe. Sans cette exception, tout
    regroupement par année, par trimestre ou par numéro de semaine serait refusé.

    Deux conditions, toutes deux portées par les données : des valeurs entières, et
    strictement croissantes. Une mesure ne croît pas strictement à chaque ligne ; un axe
    ordinal, si — et un `ORDER BY` l'a mis dans cet ordre.
    """
    valeurs = [l[i] for l in lignes]
    if any(v is None or not _est_numerique(v) for v in valeurs):
        return False
    if any(float(v) != int(v) for v in valeurs):
        return False
    return all(int(a) < int(b) for a, b in zip(valeurs, valeurs[1:]))


def _indice_abscisse(
    lignes: Sequence[Sequence[Any]], numeriques: Sequence[int], total: int
) -> int | None:
    """La colonne portée en abscisse : une non numérique, sinon une ordinale."""
    non_numeriques = [i for i in range(total) if i not in numeriques]
    if non_numeriques:
        return non_numeriques[0]
    # Une seule colonne ordinale ne suffit pas : il faut qu'il reste une mesure à tracer.
    if len(numeriques) > 1 and _est_ordinale(lignes, numeriques[0]):
        return numeriques[0]
    return None


def refus(colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]) -> str | None:
    """Le motif pour lequel ce résultat ne se trace pas, ou `None` s'il se trace.

    Séparé de `proposer()` pour être testable motif par motif : un refus qui ne dirait que
    « non » rendrait indistinguables sept règles différentes, et le jour où l'une cesse de
    mordre, rien ne le signalerait.
    """
    if not lignes:
        return "résultat vide"
    if len(lignes) < 2:
        return "une seule ligne ne fait ni une évolution ni une comparaison"
    if not colonnes:
        return "aucune colonne"

    numeriques = [i for i in range(len(colonnes)) if _colonne_numerique(lignes, i)]
    if not numeriques:
        return "aucune colonne numérique à porter en ordonnée"

    x = _indice_abscisse(lignes, numeriques, len(colonnes))
    if x is None:
        return "aucune colonne de catégorie, de date ou d'ordinal à porter en abscisse"

    numeriques = [i for i in numeriques if i != x]
    if not numeriques:
        return "aucune colonne numérique à porter en ordonnée"

    etiquettes = [l[x] for l in lignes]
    # L'abscisse doit identifier la ligne. Sinon le résultat n'est pas une série : c'est
    # le cas d'une table lue à un grain plus fin que celui qu'on croit tracer — par
    # exemple une table hebdomadaire qui porte aussi une seconde dimension. Superposer
    # ces lignes produirait un graphique lisible et faux.
    if len(set(etiquettes)) != len(etiquettes):
        return "l'abscisse se répète : le résultat n'est pas une série par ligne"

    if len(numeriques) > MAX_SERIES:
        return f"{len(numeriques)} séries : au-delà de {MAX_SERIES}, aucune n'est lisible"

    if not _est_temporelle(lignes, x) and len(lignes) > MAX_CATEGORIES:
        return f"{len(lignes)} catégories en abscisse : au-delà de {MAX_CATEGORIES}"

    if len(numeriques) > 2 and _echelles_incompatibles(lignes, numeriques):
        # Deux séries d'ordres de grandeur éloignés se lisent sur deux axes. Trois ou plus,
        # non : c'est le cas « GRP, impressions et clics sur le même graphique », que les
        # unités du jeu de données rendent dénué de sens.
        return "séries d'ordres de grandeur incompatibles, sans axe commun possible"

    return None


def _mediane(lignes: Sequence[Sequence[Any]], i: int) -> float:
    valeurs = [abs(float(l[i])) for l in lignes if l[i] is not None and float(l[i]) != 0]
    return statistics.median(valeurs) if valeurs else 0.0


def _echelles_incompatibles(
    lignes: Sequence[Sequence[Any]], numeriques: Sequence[int]
) -> bool:
    medianes = [m for m in (_mediane(lignes, i) for i in numeriques) if m > 0]
    if len(medianes) < 2:
        return False
    return max(medianes) / min(medianes) > FACTEUR_SECOND_AXE


def proposer(
    colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]
) -> Graphique | None:
    """La spécification du graphique, ou `None` si ce résultat ne se trace pas.

    L'abscisse est la première colonne non numérique ; les suivantes sont ignorées plutôt
    que tracées, la règle d'unicité ci-dessus ayant déjà écarté les résultats où elles
    portaient une seconde dimension.
    """
    if refus(colonnes, lignes) is not None:
        return None

    numeriques = [i for i in range(len(colonnes)) if _colonne_numerique(lignes, i)]
    x = _indice_abscisse(lignes, numeriques, len(colonnes))
    numeriques = [i for i in numeriques if i != x]

    # Le second axe ne sert que pour exactement deux séries : à trois, un lecteur ne sait
    # plus quelle courbe se lit sur quel axe, et le refus est déjà tombé plus haut.
    second = (
        len(numeriques) == 2 and _echelles_incompatibles(lignes, numeriques)
    )
    petite = (
        min(numeriques, key=lambda i: _mediane(lignes, i)) if second else None
    )

    return Graphique(
        # Une abscisse ordinale (année, trimestre, numéro de semaine) est un continuum
        # ordonné au même titre qu'une date : la courbe y est la bonne lecture. Seule une
        # abscisse catégorielle appelle des barres.
        type=(
            COURBE
            if _est_temporelle(lignes, x) or _est_ordinale(lignes, x)
            else BARRES
        ),
        x=colonnes[x],
        etiquettes=tuple(l[x] for l in lignes),
        series=tuple(
            Serie(
                colonne=colonnes[i],
                valeurs=tuple(
                    None if l[i] is None else float(l[i]) for l in lignes
                ),
                axe_secondaire=(i == petite),
            )
            for i in numeriques
        ),
    )
