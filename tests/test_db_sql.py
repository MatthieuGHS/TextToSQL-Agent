"""Tests du point d'accès SQL.

Trois familles, dans cet ordre d'importance :

1. **Ce qui doit être refusé** — la raison d'être du module.
2. **Ce qui doit passer** — un contrôle qui bloque tout est aussi inutile qu'un contrôle
   qui ne bloque rien. Les fonctions analytiques y figurent explicitement : ce sont elles
   qui ont motivé le choix de DuckDB.
3. **Ce que le modèle lit** — les messages d'erreur sont ce qui lui permet de se
   reprendre, donc ils se testent comme le reste.
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest

from src.db import connexion, sql


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    chemin = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'tv',  1000.0), "
        "(DATE '2024-09-09', 'tv',   500.0), "
        "(DATE '2024-09-09', 'sea', 250.0)) t(step_date, channel, cost)"
    )
    con.execute(
        "CREATE TABLE kpi_compteurs AS SELECT DATE '2024-09-02' AS step_date, 42 AS mes"
    )
    con.close()
    return chemin


@pytest.fixture
def con(base):
    c = connexion.ouvrir(base)
    yield c
    c.close()


# --- 1. Ce qui doit être refusé -------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE media",
        "INSERT INTO media VALUES (DATE '2024-01-01', 'x', 1)",
        "UPDATE media SET cost = 0",
        "DELETE FROM media",
        "CREATE TABLE x AS SELECT 1",
        "ALTER TABLE media RENAME TO autre",
        "ATTACH '/tmp/autre.db' AS autre",
        "INSTALL httpfs",
        "LOAD httpfs",
        "COPY media TO '/tmp/fuite.csv'",
        "SET enable_external_access = true",
        "CALL pragma_version()",
    ],
)
def test_instruction_non_lecture_refusee(con, query):
    with pytest.raises(sql.SqlRefuse):
        sql.run_sql(query, con)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1; DROP TABLE media",
        "SELECT 1; -- ni vu ni connu\nDROP TABLE media",
        "SELECT 1;\n\n  DELETE FROM media;",
    ],
)
def test_requetes_empilees_refusees(con, query):
    """Une regex se ferait berner par le commentaire ; le parseur, non."""
    with pytest.raises(sql.SqlRefuse, match="Une seule requête"):
        sql.run_sql(query, con)


def test_lecture_de_fichier_refusee_par_la_connexion(con):
    """Typée SELECT, donc acceptée par la validation — c'est la couche 1 qui l'arrête.

    Illustre pourquoi les deux couches sont nécessaires : ni l'une ni l'autre ne suffit.
    """
    with pytest.raises(sql.SqlInvalide):
        sql.run_sql("SELECT * FROM read_csv_auto('/etc/hostname')", con)


def test_requete_non_analysable(con):
    with pytest.raises(sql.SqlInvalide, match="non analysable"):
        sql.run_sql("SELCT 1", con)


def test_requete_vide(con):
    with pytest.raises(sql.SqlInvalide):
        sql.run_sql("   ", con)


# --- 2. Ce qui doit passer ------------------------------------------------------------


def test_lecture_simple(con):
    r = sql.run_sql("SELECT channel, cost FROM media ORDER BY cost DESC", con)

    assert r.colonnes == ["channel", "cost"]
    assert len(r) == 3
    assert not r.tronque


def test_agregation(con):
    r = sql.run_sql("SELECT SUM(cost) FROM media", con)

    assert float(r.lignes[0][0]) == 1750.0


def test_cte(con):
    r = sql.run_sql("WITH x AS (SELECT * FROM media) SELECT COUNT(*) FROM x", con)

    assert r.lignes[0][0] == 3


def test_fonctions_analytiques(con):
    """Les fonctions qui ont motivé le choix de DuckDB doivent rester accessibles."""
    sql.run_sql("SELECT CORR(cost, cost), STDDEV(cost) FROM media", con)
    sql.run_sql("SELECT QUARTER(step_date), COUNT(*) FROM media GROUP BY 1", con)


def test_jointure_entre_grains(con):
    r = sql.run_sql(
        "SELECT m.step_date, SUM(m.cost), MAX(k.mes) FROM media m "
        "JOIN kpi_compteurs k ON m.step_date = k.step_date GROUP BY 1",
        con,
    )

    assert len(r) == 1


def test_limit_deja_present_dans_la_requete(con):
    """L'enveloppement ne doit pas entrer en conflit avec un LIMIT du modèle."""
    r = sql.run_sql("SELECT * FROM media ORDER BY cost DESC LIMIT 2", con)

    assert len(r) == 2


def test_point_virgule_final_tolere(con):
    r = sql.run_sql("SELECT COUNT(*) FROM media;", con)

    assert r.lignes[0][0] == 3


@pytest.mark.parametrize(
    "query",
    [
        "SELECT channel, cost FROM media -- total par canal\n",
        "SELECT channel, cost FROM media -- total par canal",
        "SELECT COUNT(*) FROM media\n-- on ne compte que l'annonceur",
    ],
)
def test_commentaire_final_ne_casse_pas_la_requete(con, query):
    """Un modèle commente son SQL en fin de ligne — c'est un motif courant, pas un cas
    tordu.

    Sans saut de ligne dans l'enveloppe, la parenthèse fermante et le `LIMIT` se
    retrouvaient commentés : le modèle recevait une erreur de syntaxe sur une requête
    pourtant correcte, et rien pour s'en sortir.
    """
    assert len(sql.run_sql(query, con)) > 0


def test_describe_passe(con):
    """Typé SELECT, et utile à un agent qui veut vérifier une colonne."""
    assert len(sql.run_sql("DESCRIBE media", con)) == 3


def test_commentaire_seul_refuse(con):
    with pytest.raises((sql.SqlInvalide, sql.SqlRefuse)):
        sql.run_sql("-- rien que du commentaire", con)


def test_ouvre_sa_connexion_si_besoin(base, monkeypatch):
    """En ligne de commande, on ne veut pas avoir à gérer la connexion soi-même."""
    monkeypatch.setattr(connexion, "chemin_base", lambda: base)

    assert sql.run_sql("SELECT COUNT(*) FROM media").lignes[0][0] == 3


# --- 3. Bornes ------------------------------------------------------------------------


def test_troncature_signalee(con):
    r = sql.run_sql("SELECT * FROM media", con, limite=2)

    assert len(r) == 2
    assert r.tronque


def test_pas_de_troncature_a_la_limite_exacte(con):
    """Le piège du hors-par-un : 3 lignes avec une limite de 3 n'est pas tronqué."""
    r = sql.run_sql("SELECT * FROM media", con, limite=3)

    assert len(r) == 3
    assert not r.tronque


def test_delai_depasse_interrompt(con):
    """Une jointure croisée non bornée doit être coupée, pas figer l'appel."""
    with pytest.raises(sql.SqlTropLong, match="interrompue"):
        sql.run_sql(
            "SELECT COUNT(*) FROM range(20000000) a, range(20000000) b", con, delai=1.0
        )


def test_la_connexion_reste_utilisable_apres_interruption(con):
    with pytest.raises(sql.SqlTropLong):
        sql.run_sql(
            "SELECT COUNT(*) FROM range(20000000) a, range(20000000) b", con, delai=1.0
        )

    assert sql.run_sql("SELECT COUNT(*) FROM media", con).lignes[0][0] == 3


# --- 4. Ce que le modèle lit ----------------------------------------------------------


def test_colonne_inconnue_liste_les_colonnes_reelles(con):
    """L'erreur la plus fréquente d'un modèle : il doit pouvoir se reprendre."""
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql("SELECT spend FROM media", con)

    message = str(exc.value)
    assert "cost" in message and "channel" in message


def test_table_inconnue_liste_les_tables(con):
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql("SELECT * FROM depenses", con)

    assert "media" in str(exc.value)


def test_l_erreur_ne_montre_pas_l_enveloppe(con):
    """Le modèle ne doit voir que la requête qu'il a écrite.

    DuckDB recopie la requête fautive dans son message : sans nettoyage, le modèle y
    verrait le `SELECT * FROM (…) LIMIT n` que nous ajoutons, et pourrait chercher à
    corriger un LIMIT qui n'est pas de lui.
    """
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql("SELECT spend FROM media", con)

    message = str(exc.value)
    assert "LIMIT 201" not in message
    assert "SELECT spend FROM media" in message


def test_message_de_refus_dit_ce_qui_est_permis(con):
    with pytest.raises(sql.SqlRefuse) as exc:
        sql.run_sql("DROP TABLE media", con)

    message = str(exc.value)
    assert "DROP" in message
    assert "SELECT" in message


# --- 5. Rendu texte -------------------------------------------------------------------


def test_rendu_tableau(con):
    texte = sql.en_texte(sql.run_sql("SELECT channel, cost FROM media", con))

    assert "channel" in texte and "cost" in texte
    assert "1000.0" in texte


def test_rendu_resultat_vide_est_une_information(con):
    texte = sql.en_texte(sql.run_sql("SELECT * FROM media WHERE channel = 'tiktok'", con))

    assert "vide" in texte.lower()


def test_rendu_annonce_la_troncature(con):
    """Une troncature silencieuse ferait énoncer un total faux en toute confiance."""
    texte = sql.en_texte(sql.run_sql("SELECT * FROM media", con, limite=2))

    assert "davantage" in texte


def test_rendu_des_nulls(con):
    texte = sql.en_texte(sql.run_sql("SELECT NULL AS vide", con))

    assert "NULL" in texte


def test_rendu_borne_en_caracteres(con):
    """Le plafond en lignes ne borne pas le coût : 200 lignes larges pèsent lourd.

    Contre-épreuve : sans budget, le même résultat dépasse la borne. Un test qui ne
    montrerait que le cas borné ne prouverait pas que la borne sert à quelque chose.
    """
    resultat = sql.run_sql("SELECT n, n * 1000 AS large FROM range(200) t(n)", con)

    borne = sql.en_texte(resultat, budget=400)
    libre = sql.en_texte(resultat, budget=10**6)

    assert len(borne) <= 400 + 200  # la ligne d'annonce s'ajoute après la coupe
    assert len(libre) > 400
    assert "davantage" in borne
    assert "davantage" not in libre


def test_rendu_garde_une_ligne_meme_hors_budget(con):
    """Un budget absurde ne doit pas produire un tableau sans données."""
    texte = sql.en_texte(sql.run_sql("SELECT * FROM media", con), budget=1)

    assert "tv" in texte or "sea" in texte
