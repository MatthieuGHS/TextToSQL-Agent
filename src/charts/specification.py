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

Trois formes de résultat se tracent, et elles couvrent ce qu'un SQL d'analyse produit :

- **large** — une abscisse, une mesure par colonne : une série par colonne ;
- **long** — une abscisse, une colonne de catégorie, une mesure : une série par
  catégorie, après pivot. C'est la forme la plus naturelle d'un `GROUP BY date, canal`,
  et la refuser privait de graphique les questions d'évolution par canal ;
- **deux mesures sans abscisse** — un nuage de points. C'est la seule réponse honnête à
  une question de corrélation : une courbe imposerait un ordre qui n'existe pas.

**Le refus est un résultat de premier ordre**, pas un cas d'erreur. Une bonne part des
questions du corpus sont des pièges, et « pas de graphique, et voici pourquoi » y est la
bonne réponse.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

# Au-delà, un axe catégoriel devient une bouillie d'étiquettes illisible. Le seuil vaut
# pour les barres seulement : une courbe temporelle de 300 points reste lisible.
MAX_CATEGORIES = 30

# Au-delà, aucune palette ne distingue plus les séries les unes des autres.
MAX_SERIES = 4

# Plafond distinct pour les séries issues d'un pivot : elles partagent la même colonne,
# donc la même unité et la même échelle — des courbes de coût par canal se lisent avec
# une légende, quatre mesures hétérogènes non. Relevé de 6 à 12 le 19/08/2026 sur défaut
# constaté : la dernière année compte 10 canaux actifs, et la question la plus attendue
# — l'évolution par canal — était refusée dans son cas nominal. À 12, un pivot sur une
# colonne à forte cardinalité (`support`, 81 valeurs) reste refusé. La palette de
# l'interface est calée sur ce maximum.
MAX_SERIES_PIVOT = 12

# En deçà, un nuage de points ne montre rien : deux points forment toujours une droite.
MIN_POINTS_NUAGE = 3

# En deçà, une distribution n'a pas de forme : les tranches porteraient une ou deux
# valeurs chacune, et l'histogramme raconterait le hasard de l'échantillon.
MIN_POINTS_HISTOGRAMME = 20

# Plafond de tranches — la règle de Freedman-Diaconis peut en demander beaucoup plus sur
# une distribution à queue lourde, et l'axe redeviendrait la bouillie d'étiquettes que
# MAX_CATEGORIES existe pour éviter.
MAX_TRANCHES = 20

# Rapport entre les **étendues** (max hors zéros) de deux séries au-delà duquel elles ne
# partagent plus un axe. L'étendue et non la médiane : l'axe se cale sur le max, donc
# c'est lui qui décide de la place laissée à l'autre série — une série en vagues (médiane
# basse, pics hauts) écrasait sa voisine sans déclencher le second axe. Mesuré sur les
# 142 requêtes distinctes du cache : le passage médiane → max ne change qu'un seul cas,
# précisément celui du défaut constaté (coût TV ×43 au-dessus des mises en service).
FACTEUR_SECOND_AXE = 25

COURBE = "courbe"
BARRES = "barres"
NUAGE = "nuage"
HISTOGRAMME = "histogramme"

_TEMPORELS = (datetime.date, datetime.datetime)


@dataclass(frozen=True)
class Serie:
    """Une colonne numérique à tracer.

    `valeurs` garde les `None` : un trou dans une série est une information — `cost` est
    NULL sur tout le SEO, non acheté et non gratuit. Les remplacer par zéro raconterait
    une chute qui n'a pas eu lieu, et c'est le genre d'erreur qu'un graphique rend
    convaincante. Après un pivot, le trou dit qu'une catégorie n'a pas de ligne pour
    cette abscisse — une semaine sans diffusion, qui ne vaut pas zéro non plus.
    """

    colonne: str
    valeurs: tuple[float | None, ...]
    axe_secondaire: bool = False


@dataclass(frozen=True)
class Graphique:
    """Pour `NUAGE`, `x` est la première mesure et `etiquettes` porte ses valeurs :
    la structure est la même que pour une courbe, seule la nature de l'abscisse change —
    numérique au lieu de temporelle ou catégorielle.

    `variantes` et `empilable` déclarent ce que l'interface a le *droit* d'offrir en
    bascule — c'est toujours le code qui décide de ce qui est licite, l'interface ne
    fait que choisir parmi le déclaré. Un continuum se lit aussi en barres ; l'inverse
    est faux, des catégories reliées par une courbe inventeraient une continuité.
    `empilable` n'est vrai que pour des séries issues d'un pivot sans second axe : même
    colonne d'origine donc même unité — la seule situation où empiler a un sens."""

    type: str
    x: str
    etiquettes: tuple[Any, ...]
    series: tuple[Serie, ...]
    variantes: tuple[str, ...] = ()
    empilable: bool = False


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
    strictement monotones — croissantes **ou décroissantes**, un `ORDER BY … DESC` étant
    une écriture aussi naturelle que l'autre ; la restreindre au sens croissant refusait
    la forme exacte que les exemples du prompt enseignent. Une mesure, elle, ne varie pas
    strictement dans un seul sens à chaque ligne.
    """
    valeurs = [l[i] for l in lignes]
    if any(v is None or not _est_numerique(v) for v in valeurs):
        return False
    if any(float(v) != int(v) for v in valeurs):
        return False
    entiers = [int(v) for v in valeurs]
    paires = list(zip(entiers, entiers[1:]))
    return all(a < b for a, b in paires) or all(a > b for a, b in paires)


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


def _etendue(lignes: Sequence[Sequence[Any]], i: int) -> float:
    """Le max hors zéros : ce sur quoi l'axe se cale, donc ce qui décide du partage."""
    valeurs = [abs(float(l[i])) for l in lignes if l[i] is not None and float(l[i]) != 0]
    return max(valeurs) if valeurs else 0.0


def _echelles_incompatibles(
    lignes: Sequence[Sequence[Any]], numeriques: Sequence[int]
) -> bool:
    etendues = [e for e in (_etendue(lignes, i) for i in numeriques) if e > 0]
    if len(etendues) < 2:
        return False
    return max(etendues) / min(etendues) > FACTEUR_SECOND_AXE


def _trier_si_ordonne(
    colonnes: Sequence[str], lignes: Sequence[Sequence[Any]], x: int
) -> list[Sequence[Any]]:
    """Rend les lignes triées par abscisse croissante quand l'abscisse est un continuum.

    Un `ORDER BY … DESC` est une écriture légitime, mais un axe du temps se lit de gauche
    à droite : le sens d'affichage est une règle de lisibilité, donc il appartient au
    code, pas à la requête. Les abscisses catégorielles gardent l'ordre de la requête —
    lui seul porte l'intention (un tri par montant décroissant, par exemple).
    """
    if _est_temporelle(lignes, x) or _est_ordinale(lignes, x):
        if all(l[x] is not None for l in lignes):
            return sorted(lignes, key=lambda l: l[x])
    return list(lignes)


def _analyser(
    colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]
) -> Graphique | str:
    """Le cœur de la décision : une spécification, ou le motif du refus.

    Une seule passe pour les deux fonctions publiques — l'ancienne paire `refus()` /
    `proposer()` recalculait chacune ses indices, et deux calculs finissent toujours par
    diverger d'une règle.
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
        if len(colonnes) == 1:
            return _histogramme(colonnes[0], lignes)
        if len(colonnes) == 2 and len(numeriques) == 2:
            return _nuage(colonnes, lignes)
        return "aucune colonne de catégorie, de date ou d'ordinal à porter en abscisse"

    mesures = [i for i in numeriques if i != x]
    if not mesures:
        return "aucune colonne numérique à porter en ordonnée"

    lignes = _trier_si_ordonne(colonnes, lignes, x)
    etiquettes = [l[x] for l in lignes]

    if len(set(etiquettes)) == len(etiquettes):
        return _large(colonnes, lignes, x, mesures)

    # L'abscisse se répète : soit le résultat est en format long — une colonne de
    # catégorie l'explique, et le pivot en fait une série par catégorie — soit il est lu
    # à un grain plus fin qu'on ne croit, et superposer ses lignes produirait un
    # graphique lisible et faux, c'est-à-dire le pire cas possible.
    categories = [
        i for i in range(len(colonnes))
        if i != x and i not in numeriques and not _est_temporelle(lignes, i)
    ]
    if len(categories) == 1 and len(mesures) == 1:
        return _pivot(colonnes, lignes, x, categories[0], mesures[0])
    return "l'abscisse se répète : le résultat n'est pas une série par ligne"


def _large(
    colonnes: Sequence[str],
    lignes: Sequence[Sequence[Any]],
    x: int,
    mesures: Sequence[int],
) -> Graphique | str:
    """Format large : une série par colonne numérique. Les règles historiques d'E7."""
    if len(mesures) > MAX_SERIES:
        return f"{len(mesures)} séries : au-delà de {MAX_SERIES}, aucune n'est lisible"

    if not _est_temporelle(lignes, x) and len(lignes) > MAX_CATEGORIES:
        return f"{len(lignes)} catégories en abscisse : au-delà de {MAX_CATEGORIES}"

    if len(mesures) > 2 and _echelles_incompatibles(lignes, mesures):
        # Deux séries d'ordres de grandeur éloignés se lisent sur deux axes. Trois ou
        # plus, non : c'est le cas « GRP, impressions et clics sur le même graphique »,
        # que les unités du jeu de données rendent dénué de sens.
        return "séries d'ordres de grandeur incompatibles, sans axe commun possible"

    # Le second axe ne sert que pour exactement deux séries : à trois, un lecteur ne sait
    # plus quelle courbe se lit sur quel axe.
    second = len(mesures) == 2 and _echelles_incompatibles(lignes, mesures)
    petite = min(mesures, key=lambda i: _etendue(lignes, i)) if second else None

    # Une abscisse ordinale (année, trimestre, numéro de semaine) est un continuum
    # ordonné au même titre qu'une date : la courbe y est la bonne lecture. Seule une
    # abscisse catégorielle appelle des barres.
    continuum = _est_temporelle(lignes, x) or _est_ordinale(lignes, x)
    return Graphique(
        type=COURBE if continuum else BARRES,
        x=colonnes[x],
        etiquettes=tuple(l[x] for l in lignes),
        series=tuple(
            Serie(
                colonne=colonnes[i],
                valeurs=tuple(None if l[i] is None else float(l[i]) for l in lignes),
                axe_secondaire=(i == petite),
            )
            for i in mesures
        ),
        variantes=(BARRES,) if continuum else (),
    )


def _pivot(
    colonnes: Sequence[str],
    lignes: Sequence[Sequence[Any]],
    x: int,
    categorie: int,
    mesure: int,
) -> Graphique | str:
    """Format long : une série par valeur de la colonne de catégorie.

    Les séries issues du pivot partagent la même colonne — donc, le plus souvent, la
    même unité. Le plus souvent seulement : mesuré sur le cache d'évaluation, le modèle
    répond à « GRP de la TV et clics du SEA » par un format long dont la catégorie est
    le **nom de la métrique**, et la colonne de valeurs mélange alors deux unités. Deux
    séries d'ordres de grandeur éloignés prennent donc deux axes, ce qui couvre ce cas.

    **Au-delà de deux séries, en revanche, l'écart d'échelle ne fait pas refuser** — et
    c'est une correction, pas un oubli. Le refus existait ici jusqu'au 19/08/2026, par
    généralisation de la règle du format large ; il s'est révélé faux le soir même sur
    le cas le plus attendu du jeu de données : onze canaux en euros, de 12 k€ à 2,7 M€,
    soit un rapport de 209 qui n'est pas un mélange d'unités mais un écart de budget
    réel. Dans un format large, deux colonnes distinctes sont deux mesures distinctes,
    et la règle d'E7 garde son sens ; ici tout vient d'une seule colonne, et refuser
    coûtait le cas nominal quand tracer ne coûte que de voir les petites séries petites.
    Le vrai piège — superposer deux grains — reste attrapé par l'unicité des couples.

    Un couple (abscisse, catégorie) dupliqué fait refuser : le résultat porte alors une
    dimension de plus que ce que le pivot croit lire — le grain caché contre lequel la
    règle d'unicité existe.
    """
    couples = [(l[x], l[categorie]) for l in lignes]
    if len(set(couples)) != len(couples):
        return "l'abscisse se répète : le résultat n'est pas une série par ligne"

    valeurs_categorie: list[Any] = []
    for l in lignes:
        if l[categorie] not in valeurs_categorie:
            valeurs_categorie.append(l[categorie])
    if len(valeurs_categorie) > MAX_SERIES_PIVOT:
        return (
            f"{len(valeurs_categorie)} séries après pivot : au-delà de "
            f"{MAX_SERIES_PIVOT}, aucune n'est lisible"
        )

    abscisses: list[Any] = []
    for l in lignes:
        if l[x] not in abscisses:
            abscisses.append(l[x])
    if not _est_temporelle(lignes, x) and len(abscisses) > MAX_CATEGORIES:
        return f"{len(abscisses)} catégories en abscisse : au-delà de {MAX_CATEGORIES}"

    # Le trou est conservé : une catégorie sans ligne pour une abscisse n'a pas de
    # valeur, et ce n'est pas zéro — les campagnes fonctionnent par vagues.
    grille = {(l[x], l[categorie]): l[mesure] for l in lignes}
    valeurs_par_categorie = {
        cat: [
            None if grille.get((a, cat)) is None else float(grille[(a, cat)])
            for a in abscisses
        ]
        for cat in valeurs_categorie
    }

    etendues = {
        cat: max((abs(v) for v in valeurs if v is not None and v != 0), default=0.0)
        for cat, valeurs in valeurs_par_categorie.items()
    }
    non_nulles = [e for e in etendues.values() if e > 0]
    incompatibles = (
        len(non_nulles) >= 2 and max(non_nulles) / min(non_nulles) > FACTEUR_SECOND_AXE
    )
    petite = (
        min(etendues, key=etendues.get)
        if incompatibles and len(valeurs_categorie) == 2
        else None
    )

    continuum = _est_temporelle(lignes, x) or _est_ordinale(lignes, x)
    return Graphique(
        type=COURBE if continuum else BARRES,
        x=colonnes[x],
        etiquettes=tuple(abscisses),
        series=tuple(
            Serie(
                colonne=str(cat),
                valeurs=tuple(valeurs_par_categorie[cat]),
                axe_secondaire=(cat == petite),
            )
            for cat in valeurs_categorie
        ),
        variantes=(BARRES,) if continuum else (),
        empilable=petite is None,
    )


def _compact(v: float) -> str:
    """« 1500000 » → « 1,5 M » : une borne de tranche se lit, elle ne se recopie pas."""
    for seuil, suffixe in ((1e9, " Md"), (1e6, " M"), (1e3, " k")):
        if abs(v) >= seuil:
            return f"{v / seuil:.3g}{suffixe}".replace(".", ",")
    return f"{v:.3g}".replace(".", ",")


def _histogramme(colonne: str, lignes: Sequence[Sequence[Any]]) -> Graphique | str:
    """Une colonne numérique seule : la forme d'une question de distribution.

    Le nombre de tranches vient de Freedman-Diaconis — largeur 2·IQR/n^⅓, un choix
    classique et robuste aux valeurs extrêmes — borné par `MAX_TRANCHES`. Le découpage
    est une décision de lisibilité : il appartient au code, pas au modèle, qui peut
    toujours binner lui-même en SQL s'il veut des tranches métier (elles arriveront
    alors comme des barres ordinaires).
    """
    valeurs = sorted(float(l[0]) for l in lignes if l[0] is not None)
    if len(valeurs) < MIN_POINTS_HISTOGRAMME:
        return (
            f"{len(valeurs)} valeur(s) : en deçà de {MIN_POINTS_HISTOGRAMME}, "
            f"une distribution n'a pas de forme"
        )
    mini, maxi = valeurs[0], valeurs[-1]
    n = len(valeurs)
    iqr = valeurs[(3 * n) // 4] - valeurs[n // 4]
    if maxi == mini or iqr == 0:
        # La masse est concentrée sur une valeur : un histogramme n'y montrerait qu'une
        # barre et du vide. Le tableau dit déjà tout.
        return "valeurs quasi constantes : une distribution n'a rien à montrer"

    largeur_fd = 2 * iqr / n ** (1 / 3)
    tranches = min(MAX_TRANCHES, max(1, math.ceil((maxi - mini) / largeur_fd)))
    largeur = (maxi - mini) / tranches

    effectifs = [0] * tranches
    for v in valeurs:
        # La borne haute appartient à la dernière tranche, sinon le max créerait la sienne.
        effectifs[min(tranches - 1, int((v - mini) / largeur))] += 1

    return Graphique(
        type=HISTOGRAMME,
        x=colonne,
        etiquettes=tuple(
            f"{_compact(mini + i * largeur)} – {_compact(mini + (i + 1) * largeur)}"
            for i in range(tranches)
        ),
        series=(Serie(colonne="effectif", valeurs=tuple(float(e) for e in effectifs)),),
    )


def _nuage(
    colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]
) -> Graphique | str:
    """Deux mesures sans abscisse : un nuage de points, jamais une courbe.

    C'est la forme d'une question de corrélation — « investissements TV contre mises en
    service » — et la seule où relier les points mentirait : l'ordre des lignes n'y porte
    aucune information. Seuls les couples complets sont tracés : un point dont une
    coordonnée manque n'est pas un point.
    """
    points = [l for l in lignes if l[0] is not None and l[1] is not None]
    if len(points) < MIN_POINTS_NUAGE:
        return (
            f"{len(points)} point(s) complet(s) : en deçà de {MIN_POINTS_NUAGE}, "
            f"un nuage ne montre rien"
        )
    return Graphique(
        type=NUAGE,
        x=colonnes[0],
        etiquettes=tuple(float(l[0]) for l in points),
        series=(
            Serie(
                colonne=colonnes[1],
                valeurs=tuple(float(l[1]) for l in points),
            ),
        ),
    )


def refus(colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]) -> str | None:
    """Le motif pour lequel ce résultat ne se trace pas, ou `None` s'il se trace.

    Séparé de `proposer()` pour être testable motif par motif : un refus qui ne dirait que
    « non » rendrait indistinguables les règles, et le jour où l'une cesse de mordre,
    rien ne le signalerait.
    """
    issue = _analyser(colonnes, lignes)
    return issue if isinstance(issue, str) else None


def proposer(
    colonnes: Sequence[str], lignes: Sequence[Sequence[Any]]
) -> Graphique | None:
    """La spécification du graphique, ou `None` si ce résultat ne se trace pas."""
    issue = _analyser(colonnes, lignes)
    return issue if isinstance(issue, Graphique) else None
