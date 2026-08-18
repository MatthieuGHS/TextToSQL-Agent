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
