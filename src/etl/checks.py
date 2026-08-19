"""Contrat de données : ce que l'ETL garantit sur la base produite.

Trois niveaux, à ne pas confondre :

- **Invariant** — doit être vrai quelle que soit la version des données. Une violation
  signifie que notre transformation est fausse, ou que la source a changé de nature.
  L'ETL plante : produire une base silencieusement incorrecte est le pire scénario,
  l'erreur ne se manifesterait qu'en aval sous forme de réponses fausses.

- **Avertissement** — particularité connue du jeu de données, ou vocabulaire inattendu.
  Journalisé, non bloquant. Ces particularités sont précisément ce que l'agent devra
  savoir détecter ; les corriger ici les rendrait invisibles.

- **Volumétrie** — simple information. Un extrait actualisé aura légitimement d'autres
  nombres de lignes ; en faire des assertions bloquerait le pipeline sans raison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb
import pandas as pd

# Sous « etl » et non sous `__name__` : ce module et `build_db` forment une seule
# pipeline, et un appelant qui veut en relayer le journal — l'interface le fait — doit
# pouvoir s'abonner à un seul arbre. Sous `__name__`, les contrôles du contrat de données
# tombaient dans une branche séparée et n'étaient relayés nulle part.
logger = logging.getLogger("etl.checks")

TABLES = ("media", "kpi_compteurs", "contexte")

# Forme du schéma que l'ETL produit — c'est notre propre contrat, pas celui de la source.
EXPECTED_COLUMNS = {"media": 14, "kpi_compteurs": 10, "contexte": 9}

# Nombre de niveaux que l'éclatement de `type` sait traiter. Doit rester aligné sur
# `transforms.TYPE_DEPTH` ; répété ici parce que ce module vérifie le schéma produit,
# sans dépendre du module qui le produit.
TYPE_DEPTH = 4

# Vocabulaire observé à ce jour. Sert d'avertissement, pas d'assertion : une valeur
# nouvelle est une information utile (la description des données fournie au modèle
# devient obsolète), pas une raison de refuser de construire la base.
KNOWN_CHANNELS = {
    "affiliation", "audio", "display", "ooh", "print",
    "radio", "sea", "seo", "social", "tv", "video",
}
KNOWN_ENTITIES = {"corporate", "pge", "hetty"}
KNOWN_PERFORMANCE_METRICS = {"clicks", "grp", "impressions"}


class DataQualityError(RuntimeError):
    """Au moins un invariant du contrat de données est violé."""


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _scalar(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]


def _set(con: duckdb.DuckDBPyConnection, sql: str) -> set:
    return {row[0] for row in con.execute(sql).fetchall()}


def assert_invariants(con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    """Vérifie les propriétés qui doivent tenir quelle que soit la version des données.

    Tous les invariants sont évalués avant de lever une exception : un seul passage
    suffit à voir tous les problèmes, plutôt que de les découvrir un par un.

    Raises:
        DataQualityError: si au moins un invariant est violé.
    """
    results: list[CheckResult] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append(CheckResult(name, bool(condition), detail))

    # --- Forme du schéma produit ---------------------------------------------------
    for table in TABLES:
        exists = _scalar(
            con, f"SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = '{table}'"
        )
        check(f"{table}: table présente", exists == 1)

        n_cols = _scalar(
            con, f"SELECT COUNT(*) FROM duckdb_columns() WHERE table_name = '{table}'"
        )
        expected = EXPECTED_COLUMNS[table]
        check(
            f"{table}: nombre de colonnes",
            n_cols == expected,
            f"attendu {expected}, obtenu {n_cols}",
        )

        n_rows = _scalar(con, f"SELECT COUNT(*) FROM {table}")
        check(f"{table}: table non vide", n_rows > 0, f"{n_rows} ligne(s)")

    # --- Intégrité des données ------------------------------------------------------
    # Identité comptable du KPI : tout ce qui entre (mises en service + changements de
    # fournisseur) se répartit entre nouveaux compteurs et déménagements.
    violations = _scalar(
        con,
        "SELECT COUNT(*) FROM kpi_compteurs "
        "WHERE mes + cdf <> new_counters_without_dem + dem",
    )
    check(
        "kpi: identité mes + cdf = new_counters + dem",
        violations == 0,
        f"{violations} ligne(s) en violation",
    )

    # `media` ne décrit qu'un annonceur. Si une livraison future en mêlait plusieurs,
    # tout agrégat sans GROUP BY brand_name deviendrait faux sans lever d'erreur — un
    # SUM(cost) additionnerait l'annonceur et ses concurrents. Invariant bloquant.
    annonceurs = _scalar(con, "SELECT COUNT(DISTINCT brand_name) FROM media")
    check(
        "media: annonceur unique",
        annonceurs == 1,
        f"{annonceurs} annonceur(s) — la table ne doit en décrire qu'un",
    )

    negatifs = _scalar(
        con, "SELECT COUNT(*) FROM media WHERE cost < 0 OR performance < 0"
    )
    check("media: aucune valeur négative", negatifs == 0, f"{negatifs} ligne(s)")

    doublons = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "  SELECT step_date, entity, channel, type, performance_metric"
        "  FROM media GROUP BY ALL HAVING COUNT(*) > 1)",
    )
    check("media: aucun doublon de clé", doublons == 0, f"{doublons} clé(s) dupliquée(s)")

    manquants = _scalar(
        con,
        "SELECT COUNT(*) FROM media "
        "WHERE step_date IS NULL OR channel IS NULL OR performance_metric IS NULL",
    )
    check(
        "media: dimensions obligatoires renseignées",
        manquants == 0,
        f"{manquants} ligne(s) incomplète(s)",
    )

    # --- Preuves que nos transformations ont fonctionné ----------------------------
    # Le marqueur de remplissage ne doit subsister ni dans `type`, ni dans les colonnes
    # issues de son éclatement.
    padding = _scalar(
        con,
        "SELECT COUNT(*) FROM media "
        "WHERE type LIKE 'none%' OR objectif = 'none' OR format = 'none'",
    )
    check(
        "media: lignes de remplissage retirées",
        padding == 0,
        f"{padding} ligne(s) portant encore le marqueur",
    )

    # Une valeur hiérarchique remplit les quatre niveaux ; une valeur plate n'en
    # remplit aucun. Toute autre combinaison signale un éclatement incohérent.
    incoherent = _scalar(
        con,
        "SELECT COUNT(*) FROM media "
        "WHERE (objectif IS NULL) <> (format IS NULL)",
    )
    check(
        "media: éclatement de type cohérent",
        incoherent == 0,
        f"{incoherent} ligne(s) partiellement éclatée(s)",
    )

    # La hiérarchie n'existe que là où la source la fournit : `format` non nul implique
    # une valeur de `type` contenant le séparateur.
    mal_eclate = _scalar(
        con,
        "SELECT COUNT(*) FROM media "
        "WHERE format IS NOT NULL AND type NOT LIKE '%||%'",
    )
    check(
        "media: hiérarchie issue de la source",
        mal_eclate == 0,
        f"{mal_eclate} ligne(s) éclatée(s) sans séparateur d'origine",
    )

    vides = _scalar(
        con, "SELECT COUNT(*) FROM contexte WHERE metric IS NULL OR metric = ''"
    )
    check("contexte: aucune métrique vide", vides == 0, f"{vides} ligne(s)")

    # --- Verdict --------------------------------------------------------------------
    echecs = [r for r in results if not r.passed]
    if echecs:
        rapport = "\n".join(f"  ✗ {r.name} — {r.detail}" for r in echecs)
        raise DataQualityError(
            f"{len(echecs)} invariant(s) violé(s) sur {len(results)} :\n{rapport}"
        )

    logger.info("contrôle  %d invariants vérifiés", len(results))
    return results


def log_volumetry(con: duckdb.DuckDBPyConnection) -> None:
    """Journalise la volumétrie et la couverture temporelle, à titre d'information."""
    for table in TABLES:
        n = _scalar(con, f"SELECT COUNT(*) FROM {table}")
        logger.info("volume    %-16s %8d lignes", table, n)

    debut, fin, semaines = con.execute(
        "SELECT MIN(step_date), MAX(step_date), COUNT(DISTINCT step_date) FROM media"
    ).fetchone()
    logger.info(
        "volume    période          %s → %s (%d semaines)",
        str(debut)[:10], str(fin)[:10], semaines,
    )

    total = _scalar(con, "SELECT SUM(cost) FROM media")
    logger.info("volume    investissement   %12.0f €", total)


# Un canal démarrant plus de LATE_START_WEEKS après le début global manque réellement
# d'historique — c'est un problème de modélisation en aval.
LATE_START_WEEKS = 8

# En deçà de ce taux d'occupation, un canal est diffusé par vagues plutôt qu'en continu.
# Ce n'est pas une anomalie (les campagnes fonctionnent ainsi), mais une semaine absente
# ne doit pas être lue comme une donnée manquante.
INTERMITTENT_RATIO = 0.80


def log_warnings(con: duckdb.DuckDBPyConnection) -> None:
    """Journalise les particularités du jeu de données et les écarts de vocabulaire."""
    _log_vocabulary_drift(con)
    _log_data_particularities(con)
    _log_coverage(con)


def log_source_coverage(
    con: duckdb.DuckDBPyConnection, master: pd.DataFrame
) -> list[str]:
    """Vérifie que rien de la source maîtresse ne se perd en route.

    L'ETL ne lit pas ``features.csv`` : il lit les vues qui en dérivent, plus complètes
    pour notre usage. Ce choix repose sur une hypothèse — les vues couvrent l'intégralité
    de la source — qui n'est vraie que tant que le client ne fait pas évoluer son extrait.

    Le risque n'est pas qu'une ligne se perde, c'est qu'une **métrique entière** existe
    dans la source sans jamais atteindre la base : l'agent affirmerait alors qu'elle
    n'existe pas. Le contrôle porte donc sur le vocabulaire de `performance_metric`.

    Les destinations sont déduites de la base produite, pas d'une liste écrite en dur :
    une métrique est couverte si elle apparaît dans `media.performance_metric` ou dans
    `contexte.metric`.

    Avertissement et non invariant : une métrique nouvelle n'est pas une erreur de notre
    transformation, c'est un enrichissement de la source. Mais elle rend obsolète la
    description des données fournie au modèle de langage, qui les énumère.

    Returns:
        Les métriques non couvertes, vide si l'hypothèse tient.
    """
    couvertes = _set(con, "SELECT DISTINCT performance_metric FROM media") | _set(
        con, "SELECT DISTINCT metric FROM contexte"
    )
    observees = set(master["performance_metric"].dropna().unique())

    manquantes = sorted(observees - couvertes)
    for metric in manquantes:
        n = int((master["performance_metric"] == metric).sum())
        logger.warning(
            "%-45s %-22s %6d ligne(s) de la source",
            "métrique source absente de la base:", metric, n,
        )
    for metric in sorted(couvertes - observees):
        logger.info("couverture métrique produite hors source maîtresse: %s", metric)

    if not manquantes:
        logger.info(
            "contrôle  %d métriques de la source maîtresse toutes couvertes",
            len(observees),
        )
    return manquantes


def _log_vocabulary_drift(con: duckdb.DuckDBPyConnection) -> None:
    """Signale les valeurs jamais observées jusqu'ici.

    Une valeur nouvelle n'est pas une erreur, mais elle rend obsolète la description des
    données fournie au modèle de langage — qui ne saurait pas qu'elle existe.
    """
    for label, column, known in [
        ("canal", "channel", KNOWN_CHANNELS),
        ("entité", "entity", KNOWN_ENTITIES),
        ("métrique", "performance_metric", KNOWN_PERFORMANCE_METRICS),
    ]:
        observed = _set(con, f"SELECT DISTINCT {column} FROM media")
        for value in sorted(observed - known):
            logger.warning("%-45s %s", f"{label} inconnu jusqu'ici:", value)
        for value in sorted(known - observed):
            logger.warning("%-45s %s", f"{label} attendu mais absent:", value)


def _log_data_particularities(con: duckdb.DuckDBPyConnection) -> None:
    particularites = [
        (
            "coût nul mais performance positive",
            "SELECT COUNT(*) FROM media WHERE performance > 0 AND cost = 0",
        ),
        (
            "coût positif mais performance nulle",
            "SELECT COUNT(*) FROM media WHERE cost > 0 AND performance = 0",
        ),
        (
            "trafic sans coût (organique)",
            "SELECT COUNT(*) FROM media WHERE cost IS NULL AND performance > 0",
        ),
        (
            # `type` est conservée telle quelle, on peut donc vérifier a posteriori que
            # son éclatement n'a rien perdu. Un séparateur fait deux caractères : le
            # nombre de niveaux vaut (longueur - longueur sans séparateurs) / 2 + 1.
            # Au-delà de TYPE_DEPTH niveaux, l'éclatement ignore silencieusement le reste.
            f"hiérarchie `type` au-delà de {TYPE_DEPTH} niveaux (surplus ignoré)",
            "SELECT COUNT(*) FROM media "
            "WHERE (LENGTH(type) - LENGTH(REPLACE(type, '||', ''))) / 2 + 1 "
            f"> {TYPE_DEPTH}",
        ),
    ]
    for label, sql in particularites:
        n = _scalar(con, sql)
        if n:
            logger.warning("%-45s %6d ligne(s)", label, n)

    # Un couple canal/entité dont *toutes* les lignes sont à zéro n'a jamais été activé.
    # C'est différent d'une semaine creuse : le segment entier est vide.
    inactifs = con.execute(
        "SELECT channel, entity, COUNT(*) FROM media "
        "GROUP BY channel, entity "
        "HAVING MAX(COALESCE(cost, 0)) = 0 AND MAX(COALESCE(performance, 0)) = 0"
    ).fetchall()
    for channel, entity, n in inactifs:
        logger.warning(
            "%-45s %6d ligne(s)", f"segment jamais activé: {channel}/{entity}", n
        )


def _log_coverage(con: duckdb.DuckDBPyConnection) -> None:
    """Signale les canaux dont la couverture temporelle est atypique.

    Deux situations distinctes : un canal qui n'existe pas au début de la période, et un
    canal présent tout du long mais diffusé par vagues.
    """
    rows = con.execute(
        """
        WITH bornes AS (
            SELECT MIN(step_date) AS debut_global, MAX(step_date) AS fin_globale
            FROM media
        )
        SELECT
            m.channel,
            MIN(m.step_date)                                       AS debut,
            COUNT(DISTINCT m.step_date)                            AS actives,
            DATE_DIFF('week', MIN(m.step_date), b.fin_globale) + 1 AS ecoulees,
            DATE_DIFF('week', b.debut_global, MIN(m.step_date))    AS retard
        FROM media m, bornes b
        GROUP BY m.channel, b.debut_global, b.fin_globale
        ORDER BY m.channel
        """
    ).fetchall()

    for channel, debut, actives, ecoulees, retard in rows:
        if retard >= LATE_START_WEEKS:
            logger.warning(
                "%-45s %6d semaines (début %s, +%d)",
                f"démarrage tardif: {channel}", actives, str(debut)[:10], retard,
            )
        if ecoulees and actives / ecoulees < INTERMITTENT_RATIO:
            logger.warning(
                "%-45s %6d actives sur %d (%.0f %%)",
                f"diffusion intermittente: {channel}",
                actives, ecoulees, 100 * actives / ecoulees,
            )
