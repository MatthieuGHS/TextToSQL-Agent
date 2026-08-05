"""Tests des assertions du harnais d'évaluation.

Les sorties d'agent sont **fabriquées à la main** : une réponse juste, une réponse
fausse. C'est délibéré et c'est l'ordre imposé par le plan — les assertions s'écrivent
avant que l'agent existe, sinon elles se calquent sur ce qu'il produit et n'attrapent
plus rien.

Chaque assertion est vérifiée dans les deux sens. Un contrôle qu'on n'a jamais vu
échouer n'est pas un contrôle.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from tests.eval import assertions as a


@pytest.fixture
def con():
    """Une base minimale, aux ordres de grandeur de la vraie.

    `media` porte le coût de l'annonceur, `contexte` celui des concurrents : c'est
    l'homonymie que plusieurs assertions servent à surveiller.
    """
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'te', 'tv',  1000.0), "
        "(DATE '2024-09-09', 'te', 'tv',   500.0), "
        "(DATE '2024-09-09', 'te', 'sea', 250.0)) "
        "t(step_date, brand_name, channel, cost)"
    )
    con.execute(
        "CREATE TABLE contexte AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'cost', 'edf', 9000.0)) "
        "t(step_date, metric, brand_name, value)"
    )
    con.execute(
        "CREATE TABLE kpi_compteurs AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'elec', 42)) t(step_date, energy, mes)"
    )
    return con


# --- Type A : valeur ------------------------------------------------------------------

REF_TOTAL = "SELECT SUM(cost) FROM media"  # 1750.0


def test_valeur_attendue_accepte_le_bon_calcul(con):
    r = a.Resultat(reponse="1 750 €", sql=["SELECT SUM(cost) FROM media"])

    assert a.ValeurAttendue(REF_TOTAL).verifier(r, con).ok


def test_valeur_attendue_refuse_un_perimetre_faux(con):
    """Le piège d'homonymie : sommer les deux tables donne un total plausible et faux."""
    r = a.Resultat(
        reponse="10 750 €",
        sql=[
            "SELECT (SELECT SUM(cost) FROM media) + (SELECT SUM(value) "
            "FROM contexte WHERE metric = 'cost')"
        ],
    )

    verdict = a.ValeurAttendue(REF_TOTAL).verifier(r, con)
    assert not verdict.ok
    assert "1,750.00" in verdict.detail


def test_valeur_attendue_refuse_une_reponse_sans_sql(con):
    """Une réponse affirmée sans requête n'est pas vérifiable, donc pas acceptable."""
    r = a.Resultat(reponse="Le total est d'environ 1 750 €.", sql=[])

    assert not a.ValeurAttendue(REF_TOTAL).verifier(r, con).ok


def test_valeur_attendue_tolere_un_ecart_de_calcul_minime(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(cost) * 1.0001 FROM media"])

    assert a.ValeurAttendue(REF_TOTAL, tolerance=0.001).verifier(r, con).ok


# --- Type B : forme du SQL ------------------------------------------------------------


def test_ne_touche_pas_detecte_la_table_interdite(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(value) FROM contexte"])

    assert not a.SqlNeTouchePas("contexte").verifier(r, con).ok
    assert a.SqlNeTouchePas("contexte").verifier(
        a.Resultat(reponse="", sql=["SELECT SUM(cost) FROM media"]), con
    ).ok


def test_utilise_table_detecte_l_absence(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(cost) FROM media"])

    assert a.SqlUtiliseTable("media").verifier(r, con).ok
    assert not a.SqlUtiliseTable("contexte").verifier(r, con).ok


@pytest.mark.parametrize(
    "sql, attendu",
    [
        ("SELECT * FROM media WHERE step_date >= CURRENT_DATE - 30", False),
        ("SELECT * FROM media WHERE step_date >= now() - INTERVAL 1 MONTH", False),
        ("SELECT * FROM media WHERE step_date >= DATE '2024-09-09'"
         " - INTERVAL 1 WEEK", True),
    ],
)
def test_date_courante(con, sql, attendu):
    """Une période relative doit partir de la fin des données, pas d'aujourd'hui."""
    r = a.Resultat(reponse="", sql=[sql])

    assert a.SqlSansDateCourante().verifier(r, con).ok is attendu


def test_jointure_sur_semaine(con):
    croise_sans_cle = "SELECT * FROM media, kpi_compteurs"
    croise_avec_cle = (
        "SELECT * FROM media m JOIN kpi_compteurs k ON m.step_date = k.step_date"
    )
    une_seule_table = "SELECT * FROM media"

    verifier = a.SqlJointureSurSemaine().verifier
    assert not verifier(a.Resultat(reponse="", sql=[croise_sans_cle]), con).ok
    assert verifier(a.Resultat(reponse="", sql=[croise_avec_cle]), con).ok
    # Une requête mono-table n'a rien à joindre : le contrôle ne doit pas se déclencher.
    assert verifier(a.Resultat(reponse="", sql=[une_seule_table]), con).ok


def test_sql_executable(con):
    verifier = a.SqlExecutable().verifier
    assert verifier(a.Resultat(reponse="", sql=["SELECT 1"]), con).ok
    assert not verifier(a.Resultat(reponse="", sql=["SELECT * FROM inexistante"]), con).ok
    assert not verifier(a.Resultat(reponse="Je pense que oui.", sql=[]), con).ok


# --- Type C : forme du texte ----------------------------------------------------------


def test_texte_contient_et_ne_contient_pas(con):
    r = a.Resultat(reponse="Corrélation n'est pas causalité ; prudence.")

    assert a.TexteContient(("causalité", "corrélation")).verifier(r, con).ok
    assert a.TexteContient(("causalité", "corrélation"), au_moins=2).verifier(r, con).ok
    assert not a.TexteContient(("ROI",)).verifier(r, con).ok
    assert a.TexteNeContientPas(("ROI", "retour sur investissement")).verifier(r, con).ok
    assert not a.TexteNeContientPas(("causalité",)).verifier(r, con).ok


def test_pas_de_graphique_sur_resultat_vide(con):
    vide = "SELECT SUM(cost) FROM media WHERE channel = 'tiktok'"
    plein = "SELECT SUM(cost) FROM media"
    verifier = a.PasDeGraphiqueSurResultatVide().verifier

    assert not verifier(a.Resultat(reponse="", sql=[vide], graphique=True), con).ok
    assert verifier(a.Resultat(reponse="", sql=[vide], graphique=False), con).ok
    assert verifier(a.Resultat(reponse="", sql=[plein], graphique=True), con).ok


# --- Traçabilité ----------------------------------------------------------------------


def test_tracabilite_accepte_un_chiffre_calcule(con):
    r = a.Resultat(reponse="Le total s'élève à 1 750 €.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok


def test_tracabilite_accepte_un_arrondi_de_presentation(con):
    """« 1,75 millier » pour 1750 : même valeur, unité de présentation différente."""
    r = a.Resultat(reponse="Environ 1,75 millier d'euros.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok


def test_tracabilite_refuse_un_chiffre_invente(con):
    """Le cas qui compte : un ordre de grandeur affirmé sans qu'aucun SQL
    ne l'ait produit."""
    r = a.Resultat(
        reponse="Le total s'élève à 1 750 €, soit un ROI de 4 320 %.", sql=[REF_TOTAL]
    )

    verdict = a.TracabiliteNumerique().verifier(r, con)
    assert not verdict.ok
    assert "4 320" in verdict.detail


def test_tracabilite_ignore_les_petits_nombres(con):
    """« 3 canaux », « 2024 » : des nombres de phrase, pas des chiffres extraits."""
    r = a.Resultat(reponse="Les 3 canaux étudiés totalisent 1 750 €.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok
