"""Tests de l'orchestration de l'ETL.

Vérifient les garanties que le script promet : écriture atomique, code de retour, et
refus de publier une base qui viole le contrat de données. Ces comportements ne se
voient pas dans les tests de transformation — ils concernent le chemin d'erreur.
"""

from __future__ import annotations

import duckdb
import pytest

from src.etl import build_db, checks


@pytest.fixture
def base_existante(tmp_path):
    """Une base déjà en place, qu'un échec ultérieur ne doit pas détruire."""
    path = tmp_path / "mmm.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE temoin AS SELECT 42 AS valeur")
    con.close()
    return path


def test_source_absente_laisse_la_base_intacte(base_existante, monkeypatch, tmp_path):
    """Un fichier source manquant fait échouer proprement, sans toucher à l'existant."""
    monkeypatch.setattr(build_db, "RAW_DIR", tmp_path / "vide")

    code = build_db.main(["--out", str(base_existante), "--quiet"])

    assert code == 1
    con = duckdb.connect(str(base_existante), read_only=True)
    assert con.execute("SELECT valeur FROM temoin").fetchone()[0] == 42
    con.close()


def test_contrat_viole_laisse_la_base_intacte(base_existante, monkeypatch):
    """Si les invariants échouent, la base précédente n'est pas remplacée."""

    def invariants_en_echec(con):
        raise checks.DataQualityError("violation simulée")

    monkeypatch.setattr(checks, "assert_invariants", invariants_en_echec)

    code = build_db.main(["--out", str(base_existante), "--quiet"])

    assert code == 1
    con = duckdb.connect(str(base_existante), read_only=True)
    assert con.execute("SELECT valeur FROM temoin").fetchone()[0] == 42
    con.close()


def test_aucun_fichier_temporaire_residuel(base_existante, monkeypatch, tmp_path):
    """Un échec ne doit pas laisser de .tmp derrière lui."""
    monkeypatch.setattr(build_db, "RAW_DIR", tmp_path / "vide")

    build_db.main(["--out", str(base_existante), "--quiet"])

    assert not list(base_existante.parent.glob("*.tmp"))


def test_construction_nominale(tmp_path):
    """Sur les vraies sources, l'ETL produit une base valide et renvoie 0."""
    if not (build_db.RAW_DIR / "features_cost.csv").exists():
        pytest.skip("fichiers sources absents de data/raw/")

    out = tmp_path / "mmm.duckdb"
    code = build_db.main(["--out", str(out), "--quiet"])

    assert code == 0
    con = duckdb.connect(str(out), read_only=True)
    tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert tables == {"media", "kpi_compteurs", "contexte"}
    con.close()


def test_idempotence_du_contenu(tmp_path):
    """Deux constructions successives produisent le même contenu.

    La comparaison porte sur les lignes, pas sur les octets : deux fichiers DuckDB
    diffèrent toujours en binaire (métadonnées internes) même à contenu identique.
    """
    if not (build_db.RAW_DIR / "features_cost.csv").exists():
        pytest.skip("fichiers sources absents de data/raw/")

    def empreinte(path):
        con = duckdb.connect(str(path), read_only=True)
        try:
            resultat = {}
            for table in ("media", "kpi_compteurs", "contexte"):
                colonnes = [
                    r[0]
                    for r in con.execute(
                        "SELECT column_name FROM duckdb_columns() "
                        f"WHERE table_name = '{table}' ORDER BY column_index"
                    ).fetchall()
                ]
                ligne = " || '|' || ".join(
                    f"COALESCE(CAST({c} AS VARCHAR), '~')" for c in colonnes
                )
                # Somme des hachages de ligne : insensible à l'ordre de stockage.
                resultat[table] = con.execute(
                    f"SELECT COUNT(*), SUM(hash({ligne})) FROM {table}"
                ).fetchone()
            return resultat
        finally:
            con.close()

    out = tmp_path / "mmm.duckdb"
    build_db.main(["--out", str(out), "--quiet"])
    premiere = empreinte(out)
    build_db.main(["--out", str(out), "--quiet"])

    assert empreinte(out) == premiere
