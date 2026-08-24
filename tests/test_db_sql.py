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
import re

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


# --- 3 bis. Les tables lues, pour l'utilisateur ---------------------------------------
#
# Demandées par le client pour voir d'un coup d'œil d'où vient un chiffre. Deux périmètres
# cohabitent dans ce jeu de données — `media` est l'annonceur seul, `contexte` le marché
# entier — et une somme entre les deux est fausse mais crédible. Savoir quelle table a
# répondu est donc une information de premier ordre, pas une décoration.
#
# Ces tests sont écrits contre le piège que la solution facile aurait laissé passer : une
# recherche de noms dans le texte de la requête.


def test_les_tables_lues_remontent_avec_le_resultat(con):
    resultat = sql.run_sql("SELECT SUM(cost) FROM media", con)

    assert resultat.tables == ["media"]


def test_une_jointure_rend_ses_deux_tables_triees(con):
    resultat = sql.run_sql(
        "SELECT m.cost, k.mes FROM kpi_compteurs k JOIN media m USING (step_date)", con
    )

    # Triées : cette liste part dans une réponse HTTP, deux exécutions doivent
    # rendre le même ordre.
    assert resultat.tables == ["kpi_compteurs", "media"]


def test_un_nom_de_table_dans_un_litteral_n_est_pas_une_table_lue(con):
    """Contre-épreuve, et la raison de passer par le parseur du moteur.

    Une recherche de `\\bmedia\\b` dans le texte compterait ici `media` deux fois : la
    table lue et la chaîne de caractères. Sur `contexte`, elle inventerait purement une
    table que la requête ne touche pas — et l'utilisateur lirait « cette réponse vient du
    marché » sur un chiffre qui vient de l'annonceur seul.
    """
    resultat = sql.run_sql(
        "SELECT channel FROM media WHERE channel ILIKE '%contexte%' "
        "OR channel = 'kpi_compteurs'",
        con,
    )

    assert resultat.tables == ["media"]


def test_une_cte_homonyme_d_une_table_n_est_pas_comptee(con):
    """DuckDB analyse une CTE comme une référence de table : le nom ne se résout
    qu'ensuite. Sans le retrait explicite des CTE, `WITH kpi_compteurs AS (…)` ferait
    croire que la table l'a été alors qu'elle n'a jamais été ouverte."""
    resultat = sql.run_sql(
        "WITH kpi_compteurs AS (SELECT 1 AS x) SELECT x FROM kpi_compteurs", con
    )

    assert resultat.tables == []


def test_une_table_absente_de_la_base_n_est_jamais_annoncee(con):
    """`duckdb_tables()` fait autorité : l'arbre peut nommer autre chose qu'une table."""
    assert sql.tables_citees("SELECT 1", con) == []
    assert sql.tables_citees("SELECT * FROM range(3)", con) == []


def test_une_requete_inanalysable_ne_fait_pas_tomber_la_reponse(con):
    """Le champ est un affichage, calculé après une requête qui a déjà réussi.

    Lever ici transformerait une réponse produite en panne. Vide plutôt que faux, et
    l'interface n'affiche alors rien.
    """
    assert sql.tables_citees("ceci n'est pas du SQL", con) == []


# --- 4. Ce que le modèle lit ----------------------------------------------------------


def test_colonne_inconnue_liste_les_colonnes_reelles(con):
    """L'erreur la plus fréquente d'un modèle : il doit pouvoir se reprendre."""
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql("SELECT spend FROM media", con)

    message = str(exc.value)
    assert "cost" in message and "channel" in message


def test_sur_une_jointure_toutes_les_tables_citees_sont_detaillees(con):
    """Le cas où une seule liste de colonnes ne suffit pas — et peut tromper.

    DuckDB nomme l'alias fautif (`Table "k" …`), jamais la table. Ne détailler qu'une des
    tables citées revenait à tirer au sort : le modèle recevait ici les colonnes de
    `media` pour une erreur portant sur `kpi_compteurs`, et repartait chercher la colonne
    au mauvais endroit. C'est pire qu'un message pauvre — c'est un message faux.

    Ce test échoue si l'on revient à une cible unique, quelle que soit celle retenue.
    """
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql(
            "SELECT k.montant FROM media m JOIN kpi_compteurs k "
            "ON k.step_date = m.step_date",
            con,
        )

    message = str(exc.value)
    assert "Colonnes de kpi_compteurs" in message, "la table en cause est absente"
    assert "mes" in message, "la colonne qui aurait permis de se reprendre est absente"
    assert "Colonnes de media" in message, "l'autre table citée reste utile au modèle"


def test_les_tables_citees_sont_enumerees_dans_l_ordre(con):
    """Le message part dans le contexte du modèle : son ordre doit être fixé.

    Rejouer l'appel dans le même processus ne prouverait rien — un ensemble Python y rend
    presque toujours le même ordre. C'est la propriété de tri qui se teste, exactement
    comme pour les énumérations du prompt : sans `sorted()`, l'ordre dépend du hachage des
    chaînes, qui change d'un processus à l'autre.
    """
    with pytest.raises(sql.SqlInvalide) as exc:
        sql.run_sql(
            "SELECT k.montant FROM media m JOIN kpi_compteurs k "
            "ON k.step_date = m.step_date",
            con,
        )

    citees = re.findall(r"^Colonnes de (\w+) :", str(exc.value), re.M)

    assert citees == sorted(citees)


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
