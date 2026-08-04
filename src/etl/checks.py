"""Contrat de données : ce que l'ETL garantit sur la base produite.

Deux natures de contrôle, à ne pas confondre :

- **Assertion** — si elle échoue, *notre transformation est fausse*. L'ETL doit planter :
  produire une base silencieusement incorrecte est le pire des scénarios, car l'erreur
  ne se manifesterait qu'en aval, sous forme de réponses fausses de l'agent.

- **Avertissement** — la source a une particularité connue et documentée. On la
  journalise pour mémoire, et on continue. Ces particularités sont précisément ce que
  l'agent devra savoir détecter ; les corriger ici les rendrait invisibles.

Les valeurs attendues ci-dessous sont issues de l'analyse du jeu de données. Elles font
office de documentation exécutable : si la source évolue, elles préviennent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb

logger = logging.getLogger(__name__)

# --- Valeurs attendues -------------------------------------------------------------

EXPECTED_ROWS = {
    "media": 12_058,          # 12 770 lignes source moins 712 lignes de remplissage
    "kpi_compteurs": 730,     # 365 semaines x 2 énergies
    "contexte": 16_790,       # 365 semaines x 46 variables
}

EXPECTED_COLUMNS = {"media": 13, "kpi_compteurs": 10, "contexte": 9}

EXPECTED_TOTAL_COST = 192_398_926.0
COST_TOLERANCE = 0.001  # 0,1 %

EXPECTED_CHANNELS = {
    "affiliation", "audio", "display", "ooh", "print",
    "radio", "sea", "seo", "social", "tv", "video",
}
EXPECTED_ENTITIES = {"corporate", "pge", "hetty"}
EXPECTED_METRICS = {"clicks", "grp", "impressions"}
EXPECTED_CONTEXT_METRIC_COUNT = 11

EXPECTED_DATE_MIN = "2018-12-31"
EXPECTED_DATE_MAX = "2025-12-29"


class DataQualityError(RuntimeError):
    """Au moins une assertion du contrat de données a échoué."""


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _scalar(con: duckdb.DuckDBPyConnection, sql: str):
    """Exécute une requête renvoyant une seule valeur."""
    return con.execute(sql).fetchone()[0]


def _set(con: duckdb.DuckDBPyConnection, sql: str) -> set:
    """Exécute une requête renvoyant une colonne, sous forme d'ensemble."""
    return {row[0] for row in con.execute(sql).fetchall()}


def run_assertions(con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    """Vérifie le contrat de données.

    Toutes les assertions sont évaluées avant de lever une exception : un seul passage
    suffit à voir tous les problèmes, plutôt que de les découvrir un par un.

    Raises:
        DataQualityError: si au moins une assertion échoue.
    """
    results: list[CheckResult] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append(CheckResult(name, bool(condition), detail))

    # --- Structure -----------------------------------------------------------------
    for table, expected in EXPECTED_ROWS.items():
        actual = _scalar(con, f"SELECT COUNT(*) FROM {table}")
        check(
            f"{table}: nombre de lignes",
            actual == expected,
            f"attendu {expected:,}, obtenu {actual:,}",
        )

    for table, expected in EXPECTED_COLUMNS.items():
        actual = _scalar(
            con,
            f"SELECT COUNT(*) FROM duckdb_columns() WHERE table_name = '{table}'",
        )
        check(
            f"{table}: nombre de colonnes",
            actual == expected,
            f"attendu {expected}, obtenu {actual}",
        )

    # --- Cohérence métier ----------------------------------------------------------
    total_cost = _scalar(con, "SELECT SUM(cost) FROM media")
    ecart = abs(total_cost - EXPECTED_TOTAL_COST) / EXPECTED_TOTAL_COST
    check(
        "media: investissement total",
        ecart <= COST_TOLERANCE,
        f"attendu ~{EXPECTED_TOTAL_COST:,.0f} €, obtenu {total_cost:,.0f} € "
        f"(écart {ecart:.3%})",
    )

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

    # Le SEO est du trafic organique : un coût y est dénué de sens.
    seo_avec_cout = _scalar(
        con, "SELECT COUNT(*) FROM media WHERE channel = 'seo' AND cost IS NOT NULL"
    )
    check(
        "media: coût du SEO toujours nul",
        seo_avec_cout == 0,
        f"{seo_avec_cout} ligne(s) SEO avec un coût renseigné",
    )

    for label, column, expected in [
        ("canaux", "channel", EXPECTED_CHANNELS),
        ("entités", "entity", EXPECTED_ENTITIES),
        ("métriques", "performance_metric", EXPECTED_METRICS),
    ]:
        actual = _set(con, f"SELECT DISTINCT {column} FROM media")
        check(
            f"media: {label}",
            actual == expected,
            f"inattendus {sorted(actual - expected)}, manquants {sorted(expected - actual)}",
        )

    date_min = str(_scalar(con, "SELECT MIN(step_date) FROM media"))[:10]
    date_max = str(_scalar(con, "SELECT MAX(step_date) FROM media"))[:10]
    check(
        "media: période couverte",
        (date_min, date_max) == (EXPECTED_DATE_MIN, EXPECTED_DATE_MAX),
        f"attendu {EXPECTED_DATE_MIN} → {EXPECTED_DATE_MAX}, obtenu {date_min} → {date_max}",
    )

    negatifs = _scalar(
        con, "SELECT COUNT(*) FROM media WHERE cost < 0 OR performance < 0"
    )
    check("media: aucune valeur négative", negatifs == 0, f"{negatifs} ligne(s)")

    doublons = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "  SELECT step_date, entity, channel, type_raw, performance_metric"
        "  FROM media GROUP BY ALL HAVING COUNT(*) > 1)",
    )
    check("media: aucun doublon de clé", doublons == 0, f"{doublons} clé(s) dupliquée(s)")

    # --- Intégrité des transformations ---------------------------------------------
    # Preuve que les lignes de remplissage ont bien été retirées : leur marqueur 'none'
    # ne doit plus polluer les valeurs distinctes qui iront décrire le schéma au modèle.
    none_restants = _scalar(
        con, "SELECT COUNT(*) FROM media WHERE objectif = 'none' OR format = 'none'"
    )
    check(
        "media: plus de marqueur de remplissage",
        none_restants == 0,
        f"{none_restants} ligne(s) portant encore 'none'",
    )

    # Preuve que l'éclatement de `type` a fonctionné sur les valeurs hiérarchiques.
    hierarchiques = _scalar(
        con, "SELECT COUNT(*) FROM media WHERE format IS NOT NULL"
    )
    check(
        "media: hiérarchie de type éclatée",
        hierarchiques > 0,
        f"{hierarchiques:,} ligne(s) hiérarchiques décodées",
    )

    # Preuve que le décodage des noms de colonnes du contexte a fonctionné.
    n_metrics = _scalar(con, "SELECT COUNT(DISTINCT metric) FROM contexte")
    check(
        "contexte: métriques décodées",
        n_metrics == EXPECTED_CONTEXT_METRIC_COUNT,
        f"attendu {EXPECTED_CONTEXT_METRIC_COUNT}, obtenu {n_metrics}",
    )

    vides = _scalar(
        con, "SELECT COUNT(*) FROM contexte WHERE metric IS NULL OR metric = ''"
    )
    check("contexte: aucune métrique vide", vides == 0, f"{vides} ligne(s)")

    # --- Verdict -------------------------------------------------------------------
    echecs = [r for r in results if not r.passed]
    if echecs:
        rapport = "\n".join(f"  ✗ {r.name} — {r.detail}" for r in echecs)
        raise DataQualityError(
            f"{len(echecs)} assertion(s) en échec sur {len(results)} :\n{rapport}"
        )

    logger.info("contrôle  %d assertions passées", len(results))
    return results


def log_warnings(con: duckdb.DuckDBPyConnection) -> None:
    """Journalise les particularités connues du jeu de données.

    Ce ne sont pas des erreurs : ce sont les anomalies que l'agent devra pouvoir
    détecter et expliquer. On les trace pour qu'une évolution de la source soit visible.
    """
    warnings = [
        (
            "coût nul mais performance positive",
            "SELECT COUNT(*) FROM media "
            "WHERE performance > 0 AND cost = 0",
        ),
        (
            "coût positif mais performance nulle",
            "SELECT COUNT(*) FROM media WHERE cost > 0 AND performance = 0",
        ),
        (
            "SEO sans coût (trafic organique, attendu)",
            "SELECT COUNT(*) FROM media WHERE channel = 'seo' AND performance > 0",
        ),
        (
            "OOH entité pge entièrement nul",
            "SELECT COUNT(*) FROM media "
            "WHERE channel = 'ooh' AND entity = 'pge' AND cost = 0 AND performance = 0",
        ),
    ]

    for label, sql in warnings:
        n = _scalar(con, sql)
        if n:
            logger.warning("%-45s %6d ligne(s)", label, n)

    _log_coverage_warnings(con)


# Un canal démarrant plus de LATE_START_WEEKS après le début global manque réellement
# d'historique — c'est un problème de modélisation en aval (Meridian).
LATE_START_WEEKS = 8

# En deçà de ce taux d'occupation, un canal est diffusé par vagues plutôt qu'en continu.
# Ce n'est pas une anomalie (les campagnes TV et vidéo fonctionnent ainsi), mais le
# modèle doit le savoir pour ne pas interpréter une semaine absente comme une donnée
# manquante.
INTERMITTENT_RATIO = 0.80


def _log_coverage_warnings(con: duckdb.DuckDBPyConnection) -> None:
    """Signale les canaux dont la couverture temporelle est atypique.

    Deux situations distinctes, à ne pas confondre :

    - **démarrage tardif** — le canal n'existe pas au début de la période ;
    - **diffusion intermittente** — le canal couvre toute la période mais n'est actif
      que par vagues, ce qui est normal pour une campagne média.
    """
    rows = con.execute(
        """
        WITH bornes AS (
            SELECT MIN(step_date) AS debut_global, MAX(step_date) AS fin_globale
            FROM media
        )
        SELECT
            m.channel,
            MIN(m.step_date)                                   AS debut,
            COUNT(DISTINCT m.step_date)                        AS semaines_actives,
            DATE_DIFF('week', MIN(m.step_date), b.fin_globale) + 1 AS semaines_ecoulees,
            DATE_DIFF('week', b.debut_global, MIN(m.step_date)) AS retard
        FROM media m, bornes b
        GROUP BY m.channel, b.debut_global, b.fin_globale
        ORDER BY m.channel
        """
    ).fetchall()

    for channel, debut, actives, ecoulees, retard in rows:
        if retard >= LATE_START_WEEKS:
            logger.warning(
                "%-45s %6d semaines (début %s, %d semaines après les autres)",
                f"démarrage tardif: {channel}",
                actives,
                str(debut)[:10],
                retard,
            )
        if ecoulees and actives / ecoulees < INTERMITTENT_RATIO:
            logger.warning(
                "%-45s %6d semaines actives sur %d (%.0f %%)",
                f"diffusion intermittente: {channel}",
                actives,
                ecoulees,
                100 * actives / ecoulees,
            )
