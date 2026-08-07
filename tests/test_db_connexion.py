"""Tests de la connexion durcie.

Ces tests sont la seule preuve que la couche 1 fait ce qu'elle prétend. Ils sont écrits
en deux temps : ce qui doit être **refusé**, et ce qui doit continuer de **fonctionner** —
un verrou qui bloque tout est aussi inutile qu'un verrou qui ne bloque rien.

Chaque rejet est doublé d'un test montrant que la même opération **passe** sans la
configuration de sécurité. Sans cette contre-épreuve, un test vert ne dirait pas si c'est
la configuration qui protège ou DuckDB qui refusait déjà.
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest

from src.db import connexion


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    """Une base minimale sur disque : les tests ne dépendent pas des données client."""
    chemin = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute("CREATE TABLE media AS SELECT 1000.0 AS cost, 'tv' AS channel")
    con.close()
    return chemin


@pytest.fixture
def con(base):
    connexion_ = connexion.ouvrir(base)
    yield connexion_
    connexion_.close()


# --- Écriture : déjà couverte par read_only, vérifiée quand même ----------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE media",
        "INSERT INTO media SELECT * FROM media",
        "UPDATE media SET cost = 0",
        "DELETE FROM media",
        "CREATE TABLE x AS SELECT 1",
        "ALTER TABLE media RENAME TO autre",
    ],
)
def test_ecriture_refusee(con, sql):
    with pytest.raises(duckdb.Error):
        con.execute(sql)


def test_attach_refuse(con, tmp_path):
    with pytest.raises(duckdb.Error):
        con.execute(f"ATTACH '{tmp_path / 'autre.db'}' AS autre")


# --- Accès au système de fichiers : ce que read_only NE bloque PAS --------------------
#
# Les quatre cas ci-dessous passent avec `read_only=True` seul. C'est la raison d'être
# de `enable_external_access=false`.


def test_exfiltration_vers_fichier_refusee(con, tmp_path):
    """`COPY … TO` écrirait le jeu de données client sur le disque."""
    cible = tmp_path / "fuite.csv"

    with pytest.raises(duckdb.Error):
        con.execute(f"COPY media TO '{cible}'")

    assert not cible.exists()


def test_lecture_d_un_fichier_du_disque_refusee(con):
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv_auto('/etc/hostname')").fetchall()


def test_enumeration_du_systeme_de_fichiers_refusee(con):
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM glob('/etc/*')").fetchall()


def test_installation_d_extension_refusee(con):
    """`httpfs` ouvrirait un accès réseau depuis le SQL."""
    with pytest.raises(duckdb.Error):
        con.execute("INSTALL httpfs")


# --- Contre-épreuve : sans la configuration, ces quatre-là passent --------------------


@pytest.mark.parametrize(
    "sql_gabarit",
    [
        "COPY media TO '{tmp}/fuite.csv'",
        "SELECT * FROM read_csv_auto('/etc/hostname')",
        "SELECT * FROM glob('/etc/*')",
    ],
)
def test_sans_durcissement_les_portes_sont_ouvertes(base, tmp_path, sql_gabarit):
    """Preuve que le verrou vient bien de nous, et non d'un refus préexistant.

    Si ce test venait à échouer, ce serait que DuckDB a durci son comportement par
    défaut — bonne nouvelle, mais qui rendrait les tests ci-dessus creux : ils
    passeraient sans que notre configuration y soit pour quoi que ce soit.
    """
    nue = duckdb.connect(str(base), read_only=True)
    try:
        nue.execute(sql_gabarit.format(tmp=tmp_path)).fetchall()
    finally:
        nue.close()


# --- Le verrou tient de l'intérieur ---------------------------------------------------


def test_le_modele_ne_peut_pas_rouvrir_la_porte(con):
    """Le point qui rend la couche 1 solide : elle n'est pas désactivable en SQL."""
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")


def test_la_porte_reste_fermee_apres_la_tentative(con, tmp_path):
    """Une tentative ratée ne doit pas laisser la connexion dans un état dégradé."""
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")

    with pytest.raises(duckdb.Error):
        con.execute(f"COPY media TO '{tmp_path / 'apres.csv'}'")


# --- Ce qui doit continuer de fonctionner ---------------------------------------------


def test_lecture_simple(con):
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_fonctions_analytiques_disponibles(con):
    """Ce sont elles qui ont motivé le choix de DuckDB : le durcissement ne doit pas
    les emporter au passage."""
    con.execute("SELECT CORR(cost, cost), STDDEV(cost) FROM media").fetchall()
    con.execute("SELECT QUARTER(DATE '2024-09-02')").fetchall()


def test_cte_et_sous_requete(con):
    con.execute("WITH x AS (SELECT * FROM media) SELECT COUNT(*) FROM x").fetchall()
    con.execute("SELECT * FROM (SELECT cost FROM media) t").fetchall()


# --- Ergonomie ------------------------------------------------------------------------


def test_base_absente_message_actionnable(tmp_path):
    with pytest.raises(FileNotFoundError, match="build_db"):
        connexion.ouvrir(tmp_path / "inexistante.duckdb")
