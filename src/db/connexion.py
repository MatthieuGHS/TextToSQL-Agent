"""Ouverture de la base : la seule couche de défense qui ne repose sur aucune analyse.

Le SQL exécuté par l'agent n'est pas écrit par un humain : il est produit par un modèle de
langage, à partir d'une question qui vient de l'utilisateur. Deux risques distincts en
découlent — l'erreur (le modèle se trompe) et le détournement (la question est formulée
pour ça). Le prompt système ne protège de ni l'un ni l'autre : c'est du texte, et le texte
se contourne.

**`read_only=True` ne suffit pas non plus.** Mesuré sur DuckDB 1.5.5 : il bloque bien
`DROP`, `INSERT`, `CREATE` et `ATTACH`, mais laisse passer `COPY … TO '<fichier>'`,
`read_csv_auto('/etc/…')`, `glob('/etc/*')` et `INSTALL`. Autrement dit, il protège la
*base* mais pas la *machine* : exfiltration du jeu de données client vers le disque,
lecture de fichiers arbitraires, énumération du système de fichiers, et installation d'une
extension qui ouvrirait ensuite un accès réseau.

La configuration ci-dessous ferme ces quatre portes, et le fait **au niveau du moteur** —
elle ne dépend d'aucune inspection de chaîne, donc d'aucune subtilité de syntaxe qu'on
aurait pu manquer. C'est la couche à poser en premier : si les suivantes sont contournées,
elle protège encore.
"""

from __future__ import annotations

import os
import pathlib

import duckdb
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Verrous appliqués par DuckDB lui-même, à l'ouverture de la connexion.
CONFIG_SECURITE = {
    # Coupe tout accès au système de fichiers et au réseau depuis le SQL : ferme d'un
    # seul geste COPY TO, read_csv_auto, glob et l'installation d'extensions.
    "enable_external_access": "false",
    # Sans ces deux-là, une fonction inconnue peut déclencher le chargement automatique
    # d'une extension — un chemin de code qu'on ne veut pas voir s'ouvrir tout seul.
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}


def chemin_base() -> pathlib.Path:
    load_dotenv(ROOT / ".env")
    return ROOT / os.getenv("DB_PATH", "data/mmm.duckdb")


def ouvrir(chemin: pathlib.Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Ouvre la base en lecture seule et durcie.

    Le verrou tient de l'intérieur : une fois la connexion ouverte,
    ``SET enable_external_access = true`` est refusé par le moteur. Le modèle ne peut donc
    pas rouvrir la porte, même s'il devine le nom du paramètre.

    Raises:
        FileNotFoundError: avec le moyen de produire la base.
    """
    chemin = pathlib.Path(chemin) if chemin is not None else chemin_base()
    if not chemin.exists():
        raise FileNotFoundError(
            f"Base absente : {chemin}\nLa construire avec : python -m src.etl.build_db"
        )
    return duckdb.connect(str(chemin), read_only=True, config=CONFIG_SECURITE)
