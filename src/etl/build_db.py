"""Construit la base DuckDB de travail à partir des fichiers sources.

Trois tables métier remplacent les quatre fichiers d'origine :

- ``media``          — investissements et performances, format long, `type` éclaté
- ``kpi_compteurs``  — l'indicateur cible, format large
- ``contexte``       — variables de contexte, dépivotées

Les fichiers ``features.csv``, ``features_cost.csv`` et ``features_context.csv`` se
recoupent : le premier est la table maître dont les deux autres sont des vues dérivées.
On ne lit donc que les vues, plus complètes pour notre usage (coût et performance côte
à côte), afin d'éviter tout double comptage.

``compteurs.csv`` fait exception : c'est une **source indépendante**, et non une vue de
``features.csv``. La table maîtresse ne contient les compteurs que des fournisseurs
alternatifs et de l'historique du marché, jamais ceux de l'annonceur suivi.

Cette hypothèse de recouvrement n'est pas une supposition passive : elle est vérifiée à
chaque construction par ``checks.log_source_coverage``, qui relit la source maîtresse et
signale toute métrique qui n'atteindrait aucune des trois tables.

Usage :
    python -m src.etl.build_db [--out CHEMIN] [--quiet] [--skip-checks]
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

import duckdb
import pandas as pd
from dotenv import load_dotenv

from src.etl import checks, transforms

logger = logging.getLogger("etl")

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"

SOURCES = {
    "media": "features_cost.csv",
    "kpi_compteurs": "compteurs.csv",
    "contexte": "features_context.csv",
}

# Source maîtresse : jamais lue pour construire, seulement pour vérifier que les vues
# ci-dessus la couvrent intégralement. Volontairement hors de SOURCES : ce n'est pas un
# intrant du pipeline, c'est une pièce à conviction.
MASTER_SOURCE = "features.csv"


def configure_logging(quiet: bool = False) -> None:
    """Journal sur la sortie standard, horodaté et nivelé."""
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def read_source(filename: str, raw_dir: pathlib.Path | None = None) -> pd.DataFrame:
    """Lit un fichier source et journalise sa volumétrie.

    Raises:
        FileNotFoundError: avec un message indiquant où déposer le fichier.
    """
    path = (raw_dir or RAW_DIR) / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Fichier source absent : {path}\n"
            f"Déposer les fichiers de données dans {RAW_DIR}/"
        )
    df = pd.read_csv(path)
    logger.info("lecture   %-28s %6d lignes", filename, len(df))
    return df


def build_tables(raw_dir: pathlib.Path | None = None) -> dict[str, pd.DataFrame]:
    """Applique les transformations et renvoie les trois tables prêtes à écrire.

    Args:
        raw_dir: dossier des sources. Paramétrable depuis E9 : l'interface construit
            depuis un dossier d'attente, et ne promeut les fichiers reçus qu'une fois la
            construction réussie. Par défaut `RAW_DIR`, ce qui laisse la ligne de commande
            inchangée.
    """
    media, n_padding = transforms.build_media(read_source(SOURCES["media"], raw_dir))
    logger.info("media     remplissage exclu           %6d lignes", n_padding)
    logger.info("media     table construite            %6d lignes", len(media))

    kpi = transforms.build_kpi(read_source(SOURCES["kpi_compteurs"], raw_dir))
    logger.info("kpi       table construite            %6d lignes", len(kpi))

    contexte = transforms.build_contexte(read_source(SOURCES["contexte"], raw_dir))
    logger.info("contexte  dépivoté                    %6d lignes", len(contexte))

    return {"media": media, "kpi_compteurs": kpi, "contexte": contexte}


def write_database(tables: dict[str, pd.DataFrame], out: pathlib.Path) -> pathlib.Path:
    """Écrit les tables dans un fichier temporaire, puis le renomme.

    L'écriture atomique garantit qu'un échec en cours de route laisse la base
    précédente intacte, au lieu de la remplacer par une base tronquée.

    Returns:
        Le chemin du fichier temporaire, à renommer après validation.
    """
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)

    con = duckdb.connect(str(tmp))
    try:
        for name, df in tables.items():
            # DuckDB résout `df` en cherchant une variable Python de ce nom dans la
            # portée appelante (mécanisme dit de « replacement scan ») : le DataFrame
            # est lu directement en mémoire, sans copie ni import.
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
    finally:
        con.close()

    return tmp


def validate(path: pathlib.Path, raw_dir: pathlib.Path | None = None) -> None:
    """Ouvre la base en lecture seule et vérifie le contrat de données.

    La source maîtresse est relue ici, et non dans ``build_tables`` : elle sert à
    vérifier le résultat, pas à le produire.
    """
    con = duckdb.connect(str(path), read_only=True)
    try:
        checks.assert_invariants(con)
        checks.log_volumetry(con)
        checks.log_warnings(con)

        master_path = (raw_dir or RAW_DIR) / MASTER_SOURCE
        if master_path.exists():
            checks.log_source_coverage(con, pd.read_csv(master_path))
        else:
            logger.warning(
                "%-45s %s", "couverture non vérifiée, source absente:", MASTER_SOURCE
            )
    finally:
        con.close()


def construire(
    out: pathlib.Path,
    raw_dir: pathlib.Path | None = None,
    *,
    verifier: bool = True,
) -> pathlib.Path:
    """La pipeline, sans ligne de commande : construit, vérifie, remplace.

    Extraite de `main()` pour que l'interface l'appelle directement. `main()` n'est plus
    qu'une enveloppe qui traduit des arguments et des exceptions en codes de sortie —
    c'est la même séparation qu'entre `ask()` et l'API.

    **Lève au lieu de rendre un code.** Un appelant qui n'est pas un terminal a besoin de
    savoir *pourquoi* ça a échoué pour le dire à son utilisateur, et un entier ne le dit
    pas. Les trois exceptions qui traversent — `DataQualityError`, `FileNotFoundError`,
    `ContextColumnError` — sont exactement les trois causes que `main()` distinguait déjà.

    Returns:
        Le chemin de la base écrite.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        tables = build_tables(raw_dir)
        tmp = write_database(tables, out)

        if verifier:
            validate(tmp, raw_dir)
        else:
            logger.warning("contrat de données non vérifié")

        # Le remplacement n'intervient qu'une fois la base validée.
        os.replace(tmp, out)
        logger.info("écrit     %-28s %6.1f Mo", out.name, out.stat().st_size / 1e6)
        return out
    finally:
        # Ne jamais laisser traîner un fichier temporaire.
        out.with_suffix(out.suffix + ".tmp").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m src.etl.build_db",
        description="Construit la base DuckDB à partir des fichiers sources.",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="chemin de la base à produire (défaut : DB_PATH du .env)",
    )
    parser.add_argument("--quiet", action="store_true", help="ne journaliser que les avertissements")
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="ne pas vérifier le contrat de données (déconseillé)",
    )
    args = parser.parse_args(argv)

    configure_logging(args.quiet)

    out = args.out or ROOT / os.getenv("DB_PATH", "data/mmm.duckdb")

    try:
        construire(out, verifier=not args.skip_checks)
        return 0
    except checks.DataQualityError as exc:
        logger.error("contrat de données non respecté\n%s", exc)
        logger.error("la base précédente n'a pas été remplacée")
        return 1
    except (FileNotFoundError, transforms.ContextColumnError) as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
