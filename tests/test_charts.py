"""Tests de la spécification de graphique.

Chaque règle de lisibilité est vue **passer sur un cas sain et mordre sur un cas
délibérément mauvais**. Une règle qui n'aurait jamais refusé ne protégerait de rien, et
c'est ici que ça se vérifie : ce module est l'endroit où les propriétés des données
deviennent exécutables au lieu de rester des phrases de prompt.

Les jeux d'essai sont inventés — aucune valeur ne vient des données client.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from src import charts

SEMAINES = [datetime.date(2024, 1, 1 + 7 * i) for i in range(4)]


def _serie_temporelle(*mesures):
    return [(SEMAINES[i], *valeurs) for i, valeurs in enumerate(zip(*mesures))]


# --- Ce qui se trace ------------------------------------------------------------------


def test_une_serie_temporelle_donne_une_courbe():
    lignes = _serie_temporelle([100.0, 200.0, 150.0, 300.0])

    g = charts.proposer(["step_date", "cost"], lignes)

    assert g is not None
    assert g.type == charts.COURBE
    assert g.x == "step_date"
    assert g.etiquettes == tuple(SEMAINES)
    assert g.series[0].valeurs == (100.0, 200.0, 150.0, 300.0)


def test_une_dimension_categorielle_donne_des_barres():
    lignes = [("tv", 100.0), ("radio", 50.0), ("display", 25.0)]

    g = charts.proposer(["channel", "cost"], lignes)

    assert g is not None and g.type == charts.BARRES


def test_une_abscisse_ordinale_est_acceptee_et_lue_comme_un_continuum():
    """`SELECT EXTRACT(year …), SUM(cost)` rend deux colonnes numériques.

    Sans l'exception ordinale, tout regroupement par année, trimestre ou numéro de semaine
    serait refusé — alors que c'est une des formes de question les plus courantes.
    """
    g = charts.proposer(["annee", "cost"], [(2023, 10.0), (2024, 20.0), (2025, 15.0)])

    assert g is not None
    assert g.type == charts.COURBE
    assert g.x == "annee"
    assert [s.colonne for s in g.series] == ["cost"]


def test_les_decimaux_de_duckdb_sont_acceptes():
    """`SUM` sur une colonne DECIMAL rend des `Decimal` : les manquer viderait la série."""
    lignes = _serie_temporelle([Decimal("100.50"), Decimal("200.25"),
                                Decimal("150.00"), Decimal("300.75")])

    g = charts.proposer(["step_date", "cost"], lignes)

    assert g is not None and g.series[0].valeurs[0] == 100.5


# --- Ce qui se refuse -----------------------------------------------------------------


def test_un_resultat_vide_ne_se_trace_pas():
    """La règle que `principes.md` énonçait sans que rien ne l'applique."""
    assert charts.refus(["step_date", "cost"], []) == "résultat vide"
    assert charts.proposer(["step_date", "cost"], []) is None


def test_une_ligne_unique_ne_fait_ni_evolution_ni_comparaison():
    assert charts.refus(["channel", "cost"], [("tv", 100.0)]) is not None


def test_un_resultat_sans_mesure_ne_se_trace_pas():
    lignes = [("tv", "grp"), ("radio", "grp"), ("display", "impressions")]

    assert "numérique" in charts.refus(["channel", "performance_metric"], lignes)


def test_une_abscisse_ordinale_decroissante_est_acceptee():
    """`ORDER BY annee DESC` est la forme exacte que les exemples du prompt enseignent.

    Vu en relecture : la monotonie n'était acceptée que croissante, donc la comparaison
    annuelle canonique du prompt était refusée — alors que les mêmes années en croissant
    passaient, et que des *dates* décroissantes passaient aussi. L'affichage, lui, est
    remis en croissant : le sens de lecture d'un axe est une règle de lisibilité.
    """
    g = charts.proposer(["annee", "cost"], [(2025, 15.0), (2024, 20.0), (2023, 10.0)])

    assert g is not None
    assert g.type == charts.COURBE
    assert g.etiquettes == (2023, 2024, 2025)
    assert g.series[0].valeurs == (10.0, 20.0, 15.0)


def test_des_dates_decroissantes_sont_tracees_en_croissant():
    """Un axe du temps se lit de gauche à droite, quel que soit l'ORDER BY."""
    lignes = list(reversed(_serie_temporelle([100.0, 200.0, 150.0, 300.0])))

    g = charts.proposer(["step_date", "cost"], lignes)

    assert g.etiquettes == tuple(SEMAINES)
    assert g.series[0].valeurs == (100.0, 200.0, 150.0, 300.0)


def test_un_ordre_categoriel_est_conserve():
    """Contre-épreuve du tri : sur des catégories, l'ordre de la requête porte
    l'intention — un tri par montant décroissant, par exemple — et ne doit pas bouger."""
    lignes = [("tv", 100.0), ("radio", 50.0), ("display", 25.0)]

    g = charts.proposer(["channel", "cost"], lignes)

    assert g.etiquettes == ("tv", "radio", "display")


# --- Le format long : une série par catégorie -----------------------------------------


def test_le_format_long_est_pivote_en_une_serie_par_categorie():
    """`GROUP BY date, canal` est la forme la plus naturelle d'une évolution par canal.

    La refuser (« l'abscisse se répète ») privait de graphique la famille de questions
    que le client attend le plus. Le pivot est sûr là où la superposition ne l'est pas :
    les séries partagent la même colonne, donc la même unité.
    """
    lignes = [
        (SEMAINES[0], "tv", 100.0), (SEMAINES[0], "radio", 10.0),
        (SEMAINES[1], "tv", 200.0), (SEMAINES[1], "radio", 20.0),
    ]

    g = charts.proposer(["step_date", "channel", "cost"], lignes)

    assert g is not None and g.type == charts.COURBE
    assert g.x == "step_date"
    assert g.etiquettes == (SEMAINES[0], SEMAINES[1])
    assert [s.colonne for s in g.series] == ["tv", "radio"]
    assert g.series[0].valeurs == (100.0, 200.0)
    assert g.series[1].valeurs == (10.0, 20.0)


def test_le_pivot_garde_les_trous():
    """Une semaine sans ligne pour un canal n'est pas un zéro : diffusion par vagues."""
    lignes = [
        (SEMAINES[0], "tv", 100.0),
        (SEMAINES[1], "tv", 200.0), (SEMAINES[1], "radio", 20.0),
    ]

    g = charts.proposer(["step_date", "channel", "cost"], lignes)

    assert g.series[1].colonne == "radio"
    assert g.series[1].valeurs == (None, 20.0)


def test_le_pivot_ne_depend_pas_de_l_ordre_des_colonnes():
    """Défaut constaté le 24/08/2026, sur la question 14 de la grille client.

    `SELECT channel, mois, clics` est la forme naturelle d'une comparaison par canal, et
    elle était refusée : `channel` passait en abscisse au seul motif qu'il arrive en
    premier. Les mêmes données dans l'autre ordre se traçaient. La forme décidait par la
    **position** et non par la **nature**, ce que ce module dit précisément ne pas faire.

    Les deux ordres sont vérifiés ici parce que l'ordre naturel était le seul testé —
    huit tests de pivot écrivaient tous l'abscisse en premier, et le jeu d'essai portait
    donc la même hypothèse que le code.
    """
    naturel = [
        (SEMAINES[0], "sea", 100.0), (SEMAINES[0], "seo", 10.0),
        (SEMAINES[1], "sea", 200.0), (SEMAINES[1], "seo", 20.0),
    ]
    inverse = [(canal, semaine, clics) for semaine, canal, clics in naturel]

    a = charts.proposer(["step_date", "channel", "clics"], naturel)
    b = charts.proposer(["channel", "step_date", "clics"], inverse)

    assert b is not None, "l'ordre des colonnes ne doit pas décider du tracé"
    assert (b.type, b.x) == (a.type, a.x) == (charts.COURBE, "step_date")
    assert [s.colonne for s in b.series] == [s.colonne for s in a.series] == ["sea", "seo"]
    assert b.series[0].valeurs == a.series[0].valeurs == (100.0, 200.0)


def test_le_repli_ne_prend_pas_une_borne_de_periode_pour_une_abscisse():
    """Contre-épreuve du repli, et la raison pour laquelle il n'agit qu'en repli.

    Préférer une colonne temporelle *d'emblée* changeait 12 décisions sur les 214
    requêtes du cache, dont 11 dégradations de cette forme exacte : un `MIN(step_date)`
    de contrôle devenait l'abscisse de barres par support qui se lisaient très bien.

    Ici l'abscisse retenue ne se répète pas, donc le repli ne s'arme jamais — c'est ce
    qui garantit qu'il ne peut rien casser de ce qui marchait.
    """
    lignes = [
        ("Radio A", 1500.0, SEMAINES[0], SEMAINES[3]),
        ("Radio B", 900.0, SEMAINES[1], SEMAINES[2]),
        ("Radio C", 300.0, SEMAINES[0], SEMAINES[1]),
    ]

    g = charts.proposer(["support", "depense", "debut", "fin"], lignes)

    assert g is not None and g.type == charts.BARRES
    assert g.x == "support"


def test_le_repli_s_abstient_devant_deux_colonnes_temporelles():
    """À deux, laquelle est l'abscisse n'est plus une question de forme mais d'intention.

    Le refus initial est alors conservé : deviner ici tracerait un graphique lisible et
    faux, c'est-à-dire le pire cas.
    """
    lignes = [
        ("sea", SEMAINES[0], SEMAINES[1], 100.0),
        ("sea", SEMAINES[1], SEMAINES[2], 200.0),
        ("seo", SEMAINES[0], SEMAINES[1], 10.0),
        ("seo", SEMAINES[1], SEMAINES[2], 20.0),
    ]

    assert "se répète" in charts.refus(["channel", "debut", "fin", "clics"], lignes)


def test_un_couple_abscisse_categorie_duplique_refuse_le_pivot():
    """Contre-épreuve : la dimension cachée reste attrapée à travers le pivot.

    Deux lignes pour le même (semaine, canal) signifient qu'une troisième dimension
    existe — et le pivot superposerait ses valeurs comme l'abscisse répétée le faisait.
    """
    lignes = [
        (SEMAINES[0], "tv", 100.0), (SEMAINES[0], "tv", 40.0),
        (SEMAINES[1], "tv", 200.0), (SEMAINES[1], "radio", 20.0),
    ]

    assert "se répète" in charts.refus(["step_date", "channel", "cost"], lignes)


def test_dix_series_de_meme_unite_se_pivotent():
    """Le cas nominal du jeu de données : dix canaux actifs sur la dernière année.

    Contre-épreuve du plafond relevé le 19/08/2026 — à 6, la question la plus attendue
    (« évolution par canal ») était refusée sur sa forme normale.
    """
    lignes = [
        (SEMAINES[i], f"canal-{c}", 100.0 + c)
        for i in range(3)
        for c in range(10)
    ]

    g = charts.proposer(["step_date", "channel", "cost"], lignes)

    assert g is not None and g.type == charts.COURBE
    assert len(g.series) == 10


def test_trop_de_categories_pivotees_est_refuse():
    seuil = charts.specification.MAX_SERIES_PIVOT
    lignes = [
        (SEMAINES[i], f"canal-{c}", float(c))
        for i in range(2)
        for c in range(seuil + 1)
    ]

    motif = charts.refus(["step_date", "channel", "cost"], lignes)

    assert motif is not None and "séries après pivot" in motif


def test_deux_series_pivotees_d_echelles_eloignees_prennent_un_second_axe():
    """La catégorie peut être un nom de métrique — GRP contre clics, mesuré sur le cache.

    La colonne de valeurs mélange alors deux unités : le pivot porte les mêmes gardes
    d'échelle que le format large, sinon il superposerait ce que la règle des unités
    interdit précisément.
    """
    lignes = [
        (SEMAINES[0], "grp", 120.0), (SEMAINES[0], "clicks", 90000.0),
        (SEMAINES[1], "grp", 150.0), (SEMAINES[1], "clicks", 110000.0),
    ]

    g = charts.proposer(["step_date", "metrique", "valeur"], lignes)

    assert g is not None
    assert [s.axe_secondaire for s in g.series] == [True, False]


def test_un_ecart_de_budget_ne_fait_pas_refuser_un_pivot():
    """Contre-épreuve d'une garde retirée le 19/08/2026, et de sa raison.

    Le refus d'un pivot pour « ordres de grandeur incompatibles » avait été généralisé
    depuis le format large ; il s'est révélé faux sur le cas le plus attendu du jeu de
    données — onze canaux en euros, de 12 k€ à 2,7 M€. Un rapport de 209 n'y est pas un
    mélange d'unités mais un écart de budget réel, et tout vient d'une seule colonne.
    Au-delà de deux séries, le pivot trace donc, quelles que soient les échelles.
    """
    lignes = [
        (SEMAINES[i], cat, val * (i + 1))
        for i in range(2)
        for cat, val in (("tv", 2_700_000.0), ("sea", 1_000_000.0),
                         ("affiliation", 12_800.0))
    ]

    g = charts.proposer(["step_date", "channel", "cost"], lignes)

    assert g is not None
    assert len(g.series) == 3
    # Aucun second axe non plus : à trois séries, un lecteur ne saurait plus laquelle se
    # lit sur quel axe — la règle est la même que pour le format large.
    assert not any(s.axe_secondaire for s in g.series)
    # Même unité, donc l'empilement garde un sens : c'est la vue « budget par canal ».
    assert g.empilable


def test_un_pivot_dont_chaque_serie_n_a_qu_un_point_est_refuse():
    """Défaut constaté le 25/08/2026, à la notation manuelle de la grille.

    Le modèle explique que trois métriques d'exposition ne sont pas comparables, puis
    le graphique automatique les met sur un axe unique : chaque canal ne porte qu'une
    seule métrique, donc chaque série n'a qu'un point, et l'abscisse *sépare* des
    unités que l'adjacence des barres invite à comparer.

    La garde est posée sur la forme — aucune série ne dépasse un point — et non sur les
    unités, que ce module ne connaît pas. Une règle qui reconnaîtrait des noms de
    métriques passerait ce cas pour échouer au suivant.
    """
    lignes = [
        ("clicks", "sea", 53_000_000.0),
        ("clicks", "seo", 46_800_000.0),
        ("grp", "tv", 37_010.0),
        ("grp", "radio", 17_521.0),
        ("impressions", "display", 3_636_000_000.0),
        ("impressions", "social", 1_443_000_000.0),
    ]

    assert "un tableau" in charts.refus(
        ["performance_metric", "channel", "exposition"], lignes
    )


def test_une_seconde_valeur_par_serie_suffit_a_faire_tracer_le_pivot():
    """Contre-épreuve du test précédent : sans la garde, le cas ci-dessus passait.

    Mêmes colonnes, même nombre de séries — seule change la présence d'un second point
    par série, c'est-à-dire la seule chose que la garde regarde. Ce qui se suit le long
    d'une abscisse se trace ; ce qui n'a qu'un point par série, non.
    """
    lignes = [
        (mois, canal, valeur * (i + 1))
        for i, mois in enumerate(SEMAINES[:2])
        for canal, valeur in (("sea", 53_000_000.0), ("seo", 46_800_000.0))
    ]

    g = charts.proposer(["step_date", "channel", "clics"], lignes)

    assert g is not None
    assert len(g.series) == 2
    assert all(len([v for v in s.valeurs if v is not None]) == 2 for s in g.series)


def test_deux_mesures_et_une_categorie_ne_se_pivotent_pas():
    """Contre-épreuve : le pivot exige une mesure unique — deux mesures et une catégorie
    rendraient ambigu ce qu'une série représente."""
    lignes = [
        (SEMAINES[0], "tv", 100.0, 5.0), (SEMAINES[0], "radio", 10.0, 2.0),
        (SEMAINES[1], "tv", 200.0, 6.0), (SEMAINES[1], "radio", 20.0, 3.0),
    ]

    assert "se répète" in charts.refus(
        ["step_date", "channel", "cost", "performance"], lignes
    )


# --- Les bascules déclarées à l'interface ---------------------------------------------


def test_un_continuum_declare_la_variante_barres_et_pas_l_inverse():
    """L'interface ne choisit que parmi le déclaré : un continuum se lit aussi en
    barres, mais relier des catégories par une courbe inventerait une continuité."""
    courbe = charts.proposer(
        ["step_date", "cost"], _serie_temporelle([100.0, 200.0, 150.0, 300.0])
    )
    barres = charts.proposer(
        ["channel", "cost"], [("tv", 100.0), ("radio", 50.0)]
    )

    assert charts.BARRES in courbe.variantes
    assert barres.variantes == ()


def test_seul_un_pivot_sans_second_axe_est_empilable():
    """Empiler exige la même unité : vrai pour un pivot (même colonne d'origine),
    faux dès qu'un second axe sépare les échelles — et faux pour le format large,
    dont les colonnes portent des grandeurs différentes."""
    meme_unite = [
        (SEMAINES[0], "tv", 100.0), (SEMAINES[0], "radio", 60.0),
        (SEMAINES[1], "tv", 200.0), (SEMAINES[1], "radio", 80.0),
    ]
    unites_eloignees = [
        (SEMAINES[0], "grp", 120.0), (SEMAINES[0], "clicks", 90000.0),
        (SEMAINES[1], "grp", 150.0), (SEMAINES[1], "clicks", 110000.0),
    ]
    large = _serie_temporelle([100.0, 200.0], [90.0, 110.0])

    assert charts.proposer(["step_date", "channel", "cost"], meme_unite).empilable
    assert not charts.proposer(["step_date", "m", "v"], unites_eloignees).empilable
    assert not charts.proposer(["step_date", "cost", "mes"], large[:2]).empilable


# --- L'histogramme --------------------------------------------------------------------


def test_une_colonne_numerique_seule_donne_un_histogramme():
    """La forme d'une question de distribution — refusée avant, faute d'abscisse."""
    lignes = [(float(v),) for v in list(range(10, 30)) + [12, 13, 13, 14, 25]]

    g = charts.proposer(["cost"], lignes)

    assert g is not None and g.type == charts.HISTOGRAMME
    assert g.x == "cost"
    assert g.series[0].colonne == "effectif"
    assert sum(g.series[0].valeurs) == len(lignes)
    assert len(g.etiquettes) <= charts.specification.MAX_TRANCHES


def test_un_echantillon_trop_petit_ne_fait_pas_de_distribution():
    """Contre-épreuve : dix valeurs ne dessinent que le hasard de l'échantillon."""
    lignes = [(float(v),) for v in range(10)]

    motif = charts.refus(["cost"], lignes)

    assert motif is not None and "distribution" in motif


def test_des_valeurs_quasi_constantes_ne_font_pas_d_histogramme():
    """Toute la masse sur une valeur : une barre et du vide, le tableau dit déjà tout."""
    lignes = [(0.0,)] * 30 + [(500.0,), (800.0,)]

    motif = charts.refus(["cost"], lignes)

    assert motif is not None and "constantes" in motif


def test_l_histogramme_ignore_les_null_sans_les_compter():
    lignes = [(float(v),) for v in range(10, 35)] + [(None,)] * 5

    g = charts.proposer(["cost"], lignes)

    assert sum(g.series[0].valeurs) == 25


# --- Le nuage de points ---------------------------------------------------------------


def test_deux_mesures_sans_abscisse_donnent_un_nuage():
    """La forme d'une question de corrélation : relier ces points mentirait, l'ordre des
    lignes ne portant aucune information."""
    lignes = [(100.0, 5.0), (200.0, 9.0), (150.0, 7.0), (300.0, 12.0)]

    g = charts.proposer(["cost_tv", "mes"], lignes)

    assert g is not None and g.type == charts.NUAGE
    assert g.x == "cost_tv"
    assert g.etiquettes == (100.0, 200.0, 150.0, 300.0)
    assert g.series[0].colonne == "mes"
    assert g.series[0].valeurs == (5.0, 9.0, 7.0, 12.0)


def test_un_point_incomplet_ne_figure_pas_dans_le_nuage():
    """Un point dont une coordonnée manque n'est pas un point à moitié : il n'existe pas."""
    lignes = [(100.0, 5.0), (None, 9.0), (150.0, None), (300.0, 12.0), (200.0, 8.0)]

    g = charts.proposer(["cost_tv", "mes"], lignes)

    assert g.etiquettes == (100.0, 300.0, 200.0)
    assert g.series[0].valeurs == (5.0, 12.0, 8.0)


def test_un_nuage_trop_pauvre_est_refuse():
    """Deux points forment toujours une droite : en deçà du plancher, rien n'est montré."""
    lignes = [(100.0, 5.0), (None, 9.0), (300.0, 12.0)]

    motif = charts.refus(["cost_tv", "mes"], lignes)

    assert motif is not None and "nuage" in motif


def test_trois_mesures_sans_abscisse_restent_refusees():
    """Contre-épreuve : le nuage n'accepte que le couple — à trois mesures, on ne sait
    plus quoi porter sur quel axe."""
    lignes = [(100.0, 5.0, 1.0), (200.0, 9.0, 2.0), (150.0, 7.0, 3.0)]

    assert "abscisse" in charts.refus(["a", "b", "c"], lignes)


def test_une_abscisse_qui_se_repete_est_refusee():
    """La règle qui attrape un résultat lu à un grain plus fin qu'on ne le croit.

    Une table hebdomadaire portant une seconde dimension rend deux lignes par semaine ; la
    valeur jointe s'y duplique. Superposer ces lignes produirait un graphique parfaitement
    lisible et faux — le pire cas possible, puisque rien ne le signale.
    """
    lignes = [
        (SEMAINES[0], 100.0), (SEMAINES[0], 200.0),
        (SEMAINES[1], 100.0), (SEMAINES[1], 300.0),
    ]

    assert "se répète" in charts.refus(["step_date", "mes"], lignes)
    assert charts.proposer(["step_date", "mes"], lignes) is None


def test_trop_de_categories_est_refuse():
    lignes = [(f"support-{i}", float(i)) for i in range(charts.specification
                                                        .MAX_CATEGORIES + 1)]

    assert "catégories" in charts.refus(["support", "cost"], lignes)


def test_une_courbe_temporelle_longue_reste_lisible():
    """Contre-épreuve du seuil précédent : il ne vaut que pour les barres.

    52 semaines en abscisse catégorielle seraient illisibles ; 52 points sur une courbe
    sont exactement ce qu'on veut voir. Sans cette distinction, la question la plus
    fréquente du corpus serait refusée.
    """
    jours = [datetime.date(2024, 1, 1) + datetime.timedelta(days=7 * i) for i in range(52)]
    lignes = [(j, float(i)) for i, j in enumerate(jours)]

    assert charts.refus(["step_date", "cost"], lignes) is None


def test_trop_de_series_est_refuse():
    mesures = [[float(i)] * 4 for i in range(charts.specification.MAX_SERIES + 1)]
    lignes = _serie_temporelle(*mesures)
    colonnes = ["step_date"] + [f"m{i}" for i in range(len(mesures))]

    assert "séries" in charts.refus(colonnes, lignes)


# --- Les unités -----------------------------------------------------------------------


def test_deux_series_d_echelles_eloignees_prennent_un_second_axe():
    """GRP et clics ne partagent pas d'axe : l'un écraserait l'autre à plat."""
    lignes = _serie_temporelle([100.0, 120.0, 90.0, 110.0],
                               [50_000.0, 52_000.0, 48_000.0, 51_000.0])

    g = charts.proposer(["step_date", "grp", "clics"], lignes)

    assert g is not None
    assert [s.axe_secondaire for s in g.series] == [True, False]


def test_deux_series_de_meme_ordre_partagent_l_axe():
    """Contre-épreuve : le second axe ne doit pas se déclencher tout le temps.

    Un coût hebdomadaire et un nombre de compteurs du même ordre se lisent ensemble, et
    les séparer sur deux axes suggérerait une différence de nature qui n'existe pas.
    """
    lignes = _serie_temporelle([10_000.0, 12_000.0, 11_000.0, 13_000.0],
                               [14_000.0, 15_000.0, 13_500.0, 16_000.0])

    g = charts.proposer(["step_date", "cost", "mes"], lignes)

    assert g is not None
    assert [s.axe_secondaire for s in g.series] == [False, False]


def test_une_serie_en_vagues_ecrasant_l_autre_prend_un_second_axe():
    """Le partage d'axe se juge à l'étendue, pas à la médiane — défaut constaté.

    Une série en vagues (médiane basse, pics hauts) laissait le critère médian muet :
    l'axe se calait sur son pic et écrasait la série voisine dans quelques pourcents de
    la hauteur. Ici les médianes hors zéros sont proches (rapport < 25) mais le pic est
    43 fois l'autre série : sans le critère d'étendue, ce test échoue — c'est la
    contre-épreuve du correctif.
    """
    vagues = [80.0, 90.0, 4300.0, 85.0]     # médiane ~87, pic 4300
    stable = [95.0, 100.0, 105.0, 100.0]    # médiane ~100, max 105
    lignes = _serie_temporelle(vagues, stable)

    g = charts.proposer(["step_date", "cout_tv", "mises_en_service"], lignes)

    assert g is not None
    assert [s.axe_secondaire for s in g.series] == [False, True]


def test_trois_series_d_echelles_incompatibles_sont_refusees():
    """« GRP, impressions et clics sur le même graphique » : deux axes n'y suffisent pas."""
    lignes = _serie_temporelle([100.0] * 4, [50_000.0] * 4, [2_000_000.0] * 4)

    assert "ordres de grandeur" in charts.refus(
        ["step_date", "grp", "clics", "impressions"], lignes
    )


# --- NULL n'est pas zéro --------------------------------------------------------------


def test_un_trou_reste_un_trou():
    """`cost` est NULL sur tout le SEO : non acheté, ce qui n'est pas gratuit.

    Remplacer ces NULL par zéro dessinerait une chute à zéro qui n'a pas eu lieu — et un
    graphique rend ce genre d'erreur convaincante.
    """
    lignes = _serie_temporelle([100.0, None, None, 300.0])

    g = charts.proposer(["step_date", "cost"], lignes)

    assert g is not None
    assert g.series[0].valeurs == (100.0, None, None, 300.0)
    assert 0.0 not in g.series[0].valeurs


def test_une_colonne_entierement_nulle_n_est_pas_une_mesure():
    lignes = _serie_temporelle([None, None, None, None])

    assert "numérique" in charts.refus(["step_date", "cost"], lignes)


def test_un_booleen_n_est_pas_une_mesure():
    """`bool` est un `int` en Python : sans l'exclure, un drapeau deviendrait une courbe."""
    lignes = _serie_temporelle([True, False, True, False])

    assert "numérique" in charts.refus(["step_date", "actif"], lignes)


@pytest.mark.parametrize("valeurs", [[1.0, 2.0], [Decimal("1"), Decimal("2")]])
def test_le_module_est_pur(valeurs):
    """Aucune entrée n'est modifiée : la même liste peut être relue ensuite."""
    lignes = [(datetime.date(2024, 1, 1), valeurs[0]),
              (datetime.date(2024, 1, 8), valeurs[1])]
    copie = [tuple(l) for l in lignes]

    charts.proposer(["step_date", "cost"], lignes)

    assert [tuple(l) for l in lignes] == copie
