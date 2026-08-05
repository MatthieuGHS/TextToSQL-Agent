"""Tests du contrat de données.

Un filet de sécurité qu'on n'a jamais vu attraper quoi que ce soit n'est pas un filet.
Chaque invariant est donc testé dans les deux sens : il passe sur une base saine, et il
**échoue** sur une base délibérément corrompue.

Les bases sont construites en mémoire, à partir de quelques lignes inventées.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from src.etl import checks


def base_saine() -> duckdb.DuckDBPyConnection:
    """Une base minimale respectant tous les invariants."""
    media = pd.DataFrame(
        {
            "step_date": pd.to_datetime(["2024-09-02", "2024-09-02", "2024-09-09"]),
            "brand_name": ["te", "te", "te"],
            "entity": ["pge", "pge", "pge"],
            "category": ["paid", "paid", "paid"],
            "typology": ["offline", "online", "offline"],
            "channel": ["tv", "sea", "tv"],
            "type": ["burst||classique||TF1||30", "brand", "burst||classique||M6||20"],
            "objectif": ["burst", None, "burst"],
            "format": ["classique", None, "classique"],
            "support": ["TF1", None, "M6"],
            "duree_sec": [30.0, None, 20.0],
            "cost": [1000.0, 500.0, 800.0],
            "performance": [2.5, 800.0, 1.9],
            "performance_metric": ["grp", "clicks", "grp"],
        }
    )
    kpi = pd.DataFrame(
        {
            "step_date": pd.to_datetime(["2024-09-02"]),
            "energy": ["elec"],
            "new_counters_without_dem": [1000],
            "dem": [200],
            "inbound": [300],
            "outbound": [100],
            "partners": [400],
            "web": [200],
            "mes": [800],
            "cdf": [400],  # 800 + 400 == 1000 + 200
        }
    )
    contexte = pd.DataFrame(
        {
            "step_date": pd.to_datetime(["2024-09-02"]),
            "metric": ["price"],
            "brand_name": ["edf"],
            "entity": ["edf"],
            "category": ["other"],
            "typology": ["offer"],
            "channel": ["elec"],
            "type": ["elec"],
            "value": [1300.40],
        }
    )

    con = duckdb.connect(":memory:")
    for name, df in [("media", media), ("kpi_compteurs", kpi), ("contexte", contexte)]:
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
    return con


def test_base_saine_passe():
    """Référence : sans corruption, tous les invariants passent."""
    con = base_saine()
    results = checks.assert_invariants(con)

    assert all(r.passed for r in results)
    assert len(results) > 10  # le contrat couvre bien plusieurs propriétés


# --- Chaque invariant doit réellement attraper sa violation -------------------------


def test_detecte_identite_kpi_violee():
    """mes + cdf doit égaler new_counters + dem."""
    con = base_saine()
    con.execute("UPDATE kpi_compteurs SET cdf = 999")

    with pytest.raises(checks.DataQualityError, match="identité"):
        checks.assert_invariants(con)


def test_detecte_annonceur_multiple():
    """`media` ne doit décrire qu'un annonceur.

    Sans cet invariant, un extrait mêlant l'annonceur et ses concurrents produirait des
    SUM(cost) additionnant les deux, sans qu'aucune erreur ne soit levée.
    """
    con = base_saine()
    con.execute("UPDATE media SET brand_name = 'edf' WHERE channel = 'sea'")

    with pytest.raises(checks.DataQualityError, match="annonceur unique"):
        checks.assert_invariants(con)


def test_detecte_valeur_negative():
    con = base_saine()
    con.execute("UPDATE media SET cost = -1 WHERE channel = 'tv'")

    with pytest.raises(checks.DataQualityError, match="négative"):
        checks.assert_invariants(con)


def test_detecte_doublon_de_cle():
    con = base_saine()
    con.execute(
        "INSERT INTO media SELECT * FROM media WHERE channel = 'sea'"
    )

    with pytest.raises(checks.DataQualityError, match="doublon"):
        checks.assert_invariants(con)


def test_detecte_dimension_manquante():
    con = base_saine()
    con.execute("UPDATE media SET channel = NULL WHERE channel = 'sea'")

    with pytest.raises(checks.DataQualityError, match="obligatoires"):
        checks.assert_invariants(con)


def test_detecte_ligne_de_remplissage_restante():
    """Le marqueur 'none' ne doit subsister ni dans type ni dans les niveaux éclatés."""
    con = base_saine()
    con.execute("UPDATE media SET type = 'none||none||none||none' WHERE channel = 'sea'")

    with pytest.raises(checks.DataQualityError, match="remplissage"):
        checks.assert_invariants(con)


def test_detecte_eclatement_incoherent():
    """Un objectif sans format signale un éclatement partiel."""
    con = base_saine()
    con.execute("UPDATE media SET format = NULL WHERE objectif IS NOT NULL")

    with pytest.raises(checks.DataQualityError, match="cohérent"):
        checks.assert_invariants(con)


def test_detecte_hierarchie_inventee():
    """Une ligne éclatée dont le `type` source n'a pas de séparateur est suspecte.

    C'est exactement le défaut que la version précédente du code produisait : des
    valeurs plates promues au rang de hiérarchie.
    """
    con = base_saine()
    con.execute(
        "UPDATE media SET objectif = 'brand', format = 'classique' WHERE type = 'brand'"
    )

    with pytest.raises(checks.DataQualityError, match="issue de la source"):
        checks.assert_invariants(con)


def test_detecte_metrique_de_contexte_vide():
    con = base_saine()
    con.execute("UPDATE contexte SET metric = ''")

    with pytest.raises(checks.DataQualityError, match="métrique vide"):
        checks.assert_invariants(con)


def test_detecte_table_vide():
    con = base_saine()
    con.execute("DELETE FROM contexte")

    with pytest.raises(checks.DataQualityError, match="non vide"):
        checks.assert_invariants(con)


def test_detecte_colonne_manquante():
    con = base_saine()
    con.execute("ALTER TABLE media DROP COLUMN support")

    with pytest.raises(checks.DataQualityError, match="nombre de colonnes"):
        checks.assert_invariants(con)


def test_rapport_liste_toutes_les_violations():
    """Plusieurs problèmes simultanés sont rapportés d'un coup, pas un par un."""
    con = base_saine()
    con.execute("UPDATE media SET cost = -1 WHERE channel = 'tv'")
    con.execute("UPDATE kpi_compteurs SET cdf = 999")

    with pytest.raises(checks.DataQualityError) as exc:
        checks.assert_invariants(con)

    message = str(exc.value)
    assert "négative" in message
    assert "identité" in message
    assert "2 invariant(s)" in message


# --- La volumétrie ne doit jamais bloquer -------------------------------------------


def test_volumetrie_ne_bloque_pas_sur_un_volume_different(caplog):
    """Un extrait plus petit ou plus grand reste valide : c'est de l'information.

    C'est le point qui rendait la version précédente fragile : le nombre de lignes y
    était asserté, donc un rafraîchissement des données cassait le pipeline.
    """
    con = base_saine()
    con.execute("DELETE FROM media WHERE channel = 'sea'")

    checks.assert_invariants(con)  # ne lève pas
    checks.log_volumetry(con)
    checks.log_warnings(con)


def test_avertit_sur_hierarchie_trop_profonde(caplog):
    """Un `type` à plus de 4 niveaux est signalé : l'éclatement en perdrait le surplus.

    `split_type_hierarchy` ne lit que les quatre premiers niveaux. Comme `type` est
    conservée telle quelle dans la table, le contrôle se fait a posteriori sur la base.
    """
    con = base_saine()
    con.execute("UPDATE media SET type = 'a||b||c||d||e' WHERE channel = 'tv'")

    checks.assert_invariants(con)  # ne lève pas : ce n'est pas une erreur de notre part
    with caplog.at_level("WARNING"):
        checks.log_warnings(con)

    assert "au-delà de 4 niveaux" in caplog.text


# --- Couverture de la source maîtresse ----------------------------------------------


def master(metrics: list[str]) -> pd.DataFrame:
    """Un features.csv minimal, réduit à la seule colonne qui nous intéresse."""
    return pd.DataFrame({"performance_metric": metrics})


def test_couverture_source_complete(caplog):
    """Si chaque métrique de la source atterrit quelque part, rien n'est signalé.

    Dans la base saine : `media` porte grp et clicks, `contexte` porte price.
    """
    con = base_saine()

    manquantes = checks.log_source_coverage(con, master(["grp", "clicks", "price"]))

    assert manquantes == []


def test_couverture_source_metrique_perdue(caplog):
    """Une métrique de la source qui n'atteint aucune table est signalée, sans bloquer.

    C'est le scénario qui rendait l'hypothèse « on ne lit que les vues » risquée : le
    client enrichit sa source, la métrique n'arrive jamais dans la base, et l'agent
    affirme de bonne foi qu'elle n'existe pas.
    """
    con = base_saine()

    with caplog.at_level("WARNING"):
        manquantes = checks.log_source_coverage(
            con, master(["grp", "clicks", "price", "nouvelle_metrique"])
        )

    assert manquantes == ["nouvelle_metrique"]
    assert "nouvelle_metrique" in caplog.text


def test_avertit_sur_vocabulaire_inconnu(caplog):
    """Un canal jamais vu est signalé sans bloquer."""
    con = base_saine()
    con.execute("UPDATE media SET channel = 'tiktok' WHERE channel = 'sea'")

    checks.assert_invariants(con)  # ne lève pas
    with caplog.at_level("WARNING"):
        checks.log_warnings(con)

    assert "tiktok" in caplog.text
