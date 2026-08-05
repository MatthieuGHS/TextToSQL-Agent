"""Tests du corpus d'évaluation.

Le corpus est une pièce de mesure : s'il est mal formé, tout ce qui en découle est faux.
Ces tests vérifient sa cohérence interne et, surtout, que **chaque SQL de référence
s'exécute et renvoie une valeur** — une assertion de type A dont la référence est vide
passerait pour un contrôle alors qu'elle n'en est pas un.
"""

from __future__ import annotations

import duckdb
import pytest

from src.etl import build_db
from tests.eval import corpus as c
from tests.eval.assertions import ValeurAttendue, _valeurs_numeriques


@pytest.fixture(scope="module")
def con():
    chemin = build_db.ROOT / "data" / "mmm.duckdb"
    if not chemin.exists():
        pytest.skip("base absente : lancer `python -m src.etl.build_db`")
    connexion = duckdb.connect(str(chemin), read_only=True)
    yield connexion
    connexion.close()


def test_toutes_les_proprietes_sont_couvertes():
    """Le tableau des propriétés du plan est couvert en entier."""
    attendues = {
        "grain distinct",
        "valeur absente, faible cardinalité",
        "valeur absente, forte cardinalité",
        "valeur présente dans une autre colonne",
        "dimension absente du schéma",
        "métrique absente pour ce canal",
        "homonymie entre tables",
        "unités non additionnables",
        "date relative",
        "NULL n'est pas zéro",
        "absence n'est pas manquant",
        "performance non attribuable",
        "corrélation n'est pas causalité",
        "résultat vide",
    }

    assert set(c.proprietes()) == attendues


def test_chaque_cas_porte_au_moins_une_assertion():
    sans = [x.question for x in c.CORPUS if not x.assertions]

    assert sans == []


def test_aucune_question_en_double():
    questions = [x.question for x in c.CORPUS]

    assert len(questions) == len(set(questions))


@pytest.mark.parametrize(
    "cas",
    [x for x in c.CORPUS if any(isinstance(a, ValeurAttendue) for a in x.assertions)],
    ids=lambda x: x.propriete,
)
def test_les_sql_de_reference_renvoient_une_valeur(cas, con):
    """Une référence vide rendrait l'assertion creuse : elle échouerait toujours.

    C'est le contrôle qui garantit que les valeurs attendues sont traçables à un SQL
    relu, et non à ce qu'un agent aurait produit.
    """
    for assertion in cas.assertions:
        if not isinstance(assertion, ValeurAttendue):
            continue
        valeurs = _valeurs_numeriques(assertion.sql_ref, con)

        assert valeurs, f"SQL de référence sans résultat : {assertion.sql_ref}"
        assert valeurs[0] != 0, f"référence nulle, cas sans portée : {assertion.sql_ref}"


def test_les_cas_a_resultat_vide_le_sont_vraiment(con):
    """Le cas « résultat vide » doit porter sur un segment réellement vide."""
    n = con.execute(
        "SELECT COUNT(*) FROM media WHERE channel = 'affiliation' AND entity = 'corporate'"
    ).fetchone()[0]

    assert n == 0


def test_les_valeurs_absentes_le_sont_vraiment(con):
    """Les questions « valeur absente » perdraient leur sens si la valeur existait."""
    for colonne, valeur in [("channel", "cinema"), ("channel", "podcast"),
                            ("support", "Arte")]:
        n = con.execute(
            f"SELECT COUNT(*) FROM media WHERE LOWER({colonne}) = LOWER(?)", [valeur]
        ).fetchone()[0]

        assert n == 0, f"{valeur} existe dans {colonne} : le cas ne teste plus rien"


def test_la_valeur_en_mauvaise_colonne_existe_bien(con):
    """Twitch doit exister comme `support` et pas comme `channel`, sinon le cas est faux."""
    comme_support = con.execute(
        "SELECT COUNT(*) FROM media WHERE support = 'Twitch'"
    ).fetchone()[0]
    comme_canal = con.execute(
        "SELECT COUNT(*) FROM media WHERE channel = 'Twitch'"
    ).fetchone()[0]

    assert comme_support > 0
    assert comme_canal == 0


# --- Jeu de contrôle ------------------------------------------------------------------


def test_le_jeu_de_controle_est_disjoint_du_corpus_de_travail():
    travail = {x.question for x in c.corpus_de_travail()}
    controle = {x.question for x in c.corpus_de_controle()}

    assert travail & controle == set()
    assert len(travail) + len(controle) == len(c.CORPUS)


def test_le_tirage_du_controle_est_reproductible():
    """Le tirage est déterministe : personne ne peut le rejouer jusqu'à obtenir le bon."""
    assert c.proprietes_de_controle() == c.proprietes_de_controle()
    assert len(c.proprietes_de_controle()) == c.TAILLE_CONTROLE
