"""Corpus d'évaluation, dérivé du modèle de données.

Les questions sont organisées par **propriété des données testée**, jamais par origine.
C'est cette organisation qui permet, en E8, de répondre à « cette modification a-t-elle
amélioré une propriété générale, ou juste cette question ? ».

Le corpus est écrit à partir du schéma. Il existe indépendamment de tout jeu de questions
fourni de l'extérieur, qui n'en devient qu'un échantillon étiqueté `source="grille"`.

Les SQL de référence se calent sur ``MAX(step_date)`` plutôt que sur une date en dur :
un rafraîchissement des données ne doit pas périmer le corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.eval.assertions import (
    ArretNormal,
    Assertion,
    PasDeGraphiqueSurResultatVide,
    SqlExecutable,
    SqlJointureSurSemaine,
    SqlNeTouchePas,
    SqlProduit,
    SqlSansDateCourante,
    SqlUtiliseTable,
    TexteContient,
    TexteNeContientPas,
    TracabiliteNumerique,
    ValeurAttendue,
)

FIN_DONNEES = "(SELECT MAX(step_date) FROM media)"

# Vocabulaire de refus : ce que l'agent doit dire quand une valeur, une dimension ou une
# métrique n'existe pas. Volontairement large — on teste qu'il signale l'absence, pas
# qu'il emploie une formule précise.
MOTS_ABSENCE = ("n'existe pas", "aucune", "aucun", "absent", "pas de", "ne figure pas",
                "n'apparaît pas", "introuvable", "non disponible", "ne contient pas")

# Il y avait ici un `MOTS_PERFORMANCE`, liste de vocabulaire interdit partout. Retiré le
# 11/08/2026 : voir la note sur `ASSERTIONS_UNIVERSELLES` en bas de fichier.


@dataclass(frozen=True)
class Cas:
    propriete: str
    question: str
    assertions: tuple[Assertion, ...]
    source: str = "corpus"
    note: str = ""


# --- 1. Grains distincts --------------------------------------------------------------

_GRAIN = (
    Cas(
        propriete="grain distinct",
        question="Comment les investissements TV et les mises en service ont-ils évolué "
                 "semaine par semaine sur la dernière année de données ?",
        note="deux tables, deux grains : la jointure doit porter sur la semaine",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            SqlJointureSurSemaine(),
            SqlUtiliseTable("kpi_compteurs"),
        ),
    ),
)

# --- 2. Valeur absente, faible cardinalité --------------------------------------------

_ABSENT_FAIBLE = (
    Cas(
        propriete="valeur absente, faible cardinalité",
        question="Quel budget avons-nous investi sur le canal cinéma ?",
        note="`channel` est énumérée dans le prompt : refuser sans requêter est correct",
        assertions=(TexteContient(MOTS_ABSENCE), TracabiliteNumerique()),
    ),
    Cas(
        propriete="valeur absente, faible cardinalité",
        question="Combien de GRP le canal podcast a-t-il générés ?",
        assertions=(TexteContient(MOTS_ABSENCE), TracabiliteNumerique()),
    ),
)

# --- 3. Valeur absente, forte cardinalité ---------------------------------------------

_ABSENT_FORT = (
    Cas(
        propriete="valeur absente, forte cardinalité",
        question="Combien avons-nous dépensé sur la chaîne Arte ?",
        note="`support` n'est pas énumérée : il faut vérifier avant de conclure",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            SqlUtiliseTable("media"),
            TexteContient(MOTS_ABSENCE),
        ),
    ),
)

# --- 4. Valeur présente, mais dans une autre colonne ----------------------------------

_MAUVAISE_COLONNE = (
    Cas(
        propriete="valeur présente dans une autre colonne",
        question="Quelles sont nos dépenses sur Twitch ?",
        note="Twitch existe comme `support`, jamais comme `channel` — répondre, "
             "en nommant la bonne colonne",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            ValeurAttendue(
                "SELECT SUM(cost) FROM media WHERE support = 'Twitch'"
            ),
            TracabiliteNumerique(),
        ),
    ),
)

# --- 5. Dimension absente du schéma ---------------------------------------------------

_DIMENSION_ABSENTE = (
    Cas(
        propriete="dimension absente du schéma",
        question="Quelle est la répartition de nos dépenses par région ?",
        note="rien à requêter : toute dimension absente du schéma n'existe pas",
        assertions=(TexteContient(MOTS_ABSENCE), TracabiliteNumerique()),
    ),
    Cas(
        propriete="dimension absente du schéma",
        question="Quels canaux touchent le plus les 18-24 ans ?",
        assertions=(TexteContient(MOTS_ABSENCE), TracabiliteNumerique()),
    ),
)

# --- 6. Métrique absente pour ce canal ------------------------------------------------

_METRIQUE_ABSENTE = (
    Cas(
        propriete="métrique absente pour ce canal",
        question="Quels sont les GRP du display ?",
        note="chaque canal n'a qu'une `performance_metric` ; le display a des impressions",
        assertions=(TexteContient(MOTS_ABSENCE + ("impression",)), TracabiliteNumerique()),
    ),
    Cas(
        propriete="métrique absente pour ce canal",
        question="Combien de clics la radio a-t-elle générés ?",
        assertions=(TexteContient(MOTS_ABSENCE + ("grp",)), TracabiliteNumerique()),
    ),
)

# --- 7. Homonymie entre tables --------------------------------------------------------

_HOMONYMIE = (
    Cas(
        propriete="homonymie entre tables",
        question="Quel est notre budget média total ?",
        note="`cost` existe aussi dans `contexte`, pour les concurrents, à un ordre de "
             "grandeur voisin. Sommer les deux donne un total plausible et faux.",
        assertions=(
            ValeurAttendue("SELECT SUM(cost) FROM media"),
            SqlNeTouchePas("contexte"),
            TracabiliteNumerique(),
        ),
    ),
    Cas(
        propriete="homonymie entre tables",
        question="Combien de GRP avons-nous achetés au total ?",
        assertions=(
            ValeurAttendue(
                "SELECT SUM(performance) FROM media WHERE performance_metric = 'grp'"
            ),
            SqlNeTouchePas("contexte"),
        ),
    ),
    Cas(
        propriete="homonymie entre tables",
        question="Comment nos investissements se comparent-ils à ceux d'EDF ?",
        note="ici les deux tables sont légitimes — c'est le cas symétrique du précédent",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            SqlUtiliseTable("contexte"),
            TracabiliteNumerique(),
        ),
    ),
)

# --- 8. Unités non additionnables -----------------------------------------------------

_UNITES = (
    Cas(
        propriete="unités non additionnables",
        question="Trace-moi sur un même graphique les GRP de la TV et les clics du SEA.",
        note="GRP, clics et impressions ne partagent aucune unité",
        assertions=(TexteContient(("unité", "comparable", "additionn", "axe")),),
    ),
    Cas(
        propriete="unités non additionnables",
        question="Quelle est la performance totale, tous canaux confondus ?",
        assertions=(TexteContient(("unité", "comparable", "additionn", "grp", "clic")),),
    ),
)

# --- 9. Date relative -----------------------------------------------------------------

_DATE_RELATIVE = (
    Cas(
        propriete="date relative",
        question="Quelles ont été nos dépenses sur les six derniers mois ?",
        note="se calcule depuis la fin des données, jamais depuis la date du jour",
        assertions=(
            SqlSansDateCourante(),
            ValeurAttendue(
                f"SELECT SUM(cost) FROM media "
                f"WHERE step_date > {FIN_DONNEES} - INTERVAL 6 MONTH"
            ),
        ),
    ),
    Cas(
        propriete="date relative",
        question="Combien avons-nous investi le trimestre dernier ?",
        assertions=(SqlSansDateCourante(), SqlExecutable()),
    ),
)

# --- 10. NULL n'est pas zéro ----------------------------------------------------------

_NULL_NEST_PAS_ZERO = (
    Cas(
        propriete="NULL n'est pas zéro",
        question="Combien nous coûte le SEO ?",
        note="`cost` est NULL sur tout le SEO : non acheté, ce qui n'est pas gratuit",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            TexteContient(("null", "non renseign", "pas de coût", "n'est pas",
                           "non acheté", "organique")),
        ),
    ),
)

# --- 11. Absence n'est pas manquant ---------------------------------------------------

_ABSENCE_VS_MANQUANT = (
    Cas(
        propriete="absence n'est pas manquant",
        question="Le canal vidéo a-t-il un historique complet ?",
        note="diffusion par vagues, pas des données manquantes — à distinguer du SEO, "
             "qui démarre réellement plus tard",
        assertions=(SqlExecutable(), TracabiliteNumerique()),
    ),
)

# --- 12. Performance non attribuable --------------------------------------------------

_PERFORMANCE = (
    Cas(
        propriete="performance non attribuable",
        question="Quel canal a le meilleur ROI ?",
        note="aucune attribution n'est possible avec ces données ; refus pédagogique",
        # `TracabiliteNumerique` porte ici la propriété que la liste de vocabulaire
        # interdit portait avant elle : un chiffre de ROI n'a aucune source possible dans
        # ces données, donc il ressort comme non traçable. Le rationnel du retrait de la
        # liste (voir la note sur `ASSERTIONS_UNIVERSELLES`) l'affirmait déjà ; il n'avait
        # pas été appliqué aux deux cas qu'il concerne au premier chef.
        assertions=(TexteNeContientPas(("le meilleur roi est", "roi de")),
                    TexteContient(MOTS_ABSENCE + ("attribu", "modélisation", "mmm")),
                    TracabiliteNumerique()),
    ),
    Cas(
        propriete="performance non attribuable",
        question="Combien de compteurs la télévision nous a-t-elle rapportés ?",
        assertions=(TexteContient(MOTS_ABSENCE + ("attribu", "modélisation", "mmm")),
                    TracabiliteNumerique()),
    ),
)

# --- 13. Corrélation n'est pas causalité ----------------------------------------------

_CAUSALITE = (
    Cas(
        propriete="corrélation n'est pas causalité",
        question="Y a-t-il un lien entre nos investissements TV et les mises en service ?",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            SqlJointureSurSemaine(),
            TexteContient(("causalité", "cause", "corrélation n'implique")),
        ),
    ),
)

# --- 14. Résultat vide ----------------------------------------------------------------

_RESULTAT_VIDE = (
    Cas(
        propriete="résultat vide",
        question="Quelles dépenses en affiliation pour l'entité corporate ?",
        note="segment jamais activé : rapporter l'absence, ne pas tracer de graphique",
        assertions=(
            SqlExecutable(),
            SqlProduit(),
            PasDeGraphiqueSurResultatVide(),
            TexteContient(MOTS_ABSENCE),
        ),
    ),
)


CORPUS: tuple[Cas, ...] = (
    *_GRAIN, *_ABSENT_FAIBLE, *_ABSENT_FORT, *_MAUVAISE_COLONNE, *_DIMENSION_ABSENTE,
    *_METRIQUE_ABSENTE, *_HOMONYMIE, *_UNITES, *_DATE_RELATIVE, *_NULL_NEST_PAS_ZERO,
    *_ABSENCE_VS_MANQUANT, *_PERFORMANCE, *_CAUSALITE, *_RESULTAT_VIDE,
)

# Une réponse produite après un abandon de la boucle n'est pas une réponse, quel que soit
# son contenu : aucune propriété du jeu de données n'en dispense. C'est la seule assertion
# qui s'applique partout.
#
# Une liste de vocabulaire interdit y figurait, et n'y figure plus. La mesure a
# tranché : sur la grille client, il condamnait les meilleures réponses — celles qui
# *expliquent* qu'un retour sur investissement ne se calcule pas avec ces données, et qui
# doivent forcément nommer la chose pour le dire. Un contrôle sur le vocabulaire ne
# distingue pas l'affirmation de sa réfutation, et se trompe précisément là où l'agent
# excelle.
#
# La propriété visée — aucune performance attribuée — reste tenue, et mieux : par
# `TracabiliteNumerique`, puisqu'un chiffre de ROI n'a aucune source possible dans ces
# données, et par les assertions de forme des cas d'attribution, qui exigent l'explication
# au lieu d'interdire le mot. Une propriété portée par les données plutôt que par une
# liste de mots.
ASSERTIONS_UNIVERSELLES: tuple[Assertion, ...] = (
    ArretNormal(),
)


def proprietes() -> list[str]:
    return sorted({c.propriete for c in CORPUS})


# --- Jeu de contrôle ------------------------------------------------------------------

# Le scellé, **écrit à la main et non recalculé**.
#
# Il l'a été : un `random.sample` de graine 20260805 sur `proprietes()`, tiré le
# 05/08/2026 quand le corpus comptait 14 propriétés. La graine reste ici comme trace de
# provenance — elle n'est plus un mécanisme.
#
# La raison du changement vaut d'être retenue. Un tirage qui se rejoue à chaque appel
# dépend du contenu courant du corpus : mesuré le 14/08/2026, l'ajout d'**une seule**
# propriété faisait sortir 2 des 3 propriétés scellées et entrer une propriété déjà jouée
# trois fois à trois niveaux d'effort. Le scellé se serait donc réattribué tout seul à E7,
# sans erreur ni avertissement, en emportant sa seule raison d'être : n'avoir jamais été vu.
#
# Corollaire, et c'est ce qui interdit d'y revenir : le scellé ne peut plus être
# recomposé. Les 17 questions du corpus de travail ont été jouées et leurs réponses lues.
# En faire entrer une ici reviendrait à sceller une enveloppe déjà ouverte. On peut
# seulement l'augmenter de questions neuves, jamais exécutées.
#
# Tirage par propriété et non par question : mettre de côté quelques questions d'une
# propriété par ailleurs travaillée ne mesurerait rien.
PROPRIETES_CONTROLE: frozenset[str] = frozenset({
    "corrélation n'est pas causalité",
    "performance non attribuable",
    "valeur absente, faible cardinalité",
})


def proprietes_de_controle() -> frozenset[str]:
    """Les propriétés mises sous scellé, à n'ouvrir qu'une fois, à la fin de E8.

    Ce que le scellé mesure, et ce qu'il ne mesure pas : les trois propriétés retenues
    sont toutes de la famille « refus expliqué », et aucun des quatre échecs relevés sur
    la ligne de base n'y a de contrepartie. Son ouverture répondra donc à « le réglage
    a-t-il cassé les refus ? », pas à « a-t-il généralisé ? ». C'est une non-régression,
    et l'annoncer comme telle vaut mieux que de lui prêter une portée qu'il n'a pas.
    """
    return PROPRIETES_CONTROLE


def corpus_de_travail() -> tuple[Cas, ...]:
    """Le corpus visible pendant le réglage."""
    controle = proprietes_de_controle()
    return tuple(c for c in CORPUS if c.propriete not in controle)


def corpus_de_controle() -> tuple[Cas, ...]:
    """Le jeu de contrôle. Ne pas exécuter avant la fin du réglage."""
    controle = proprietes_de_controle()
    return tuple(c for c in CORPUS if c.propriete in controle)
