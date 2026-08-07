"""Description des données, générée depuis la base.

Un prompt écrit à la main périme : il énonce des faits — la liste des canaux, les bornes
temporelles, les métriques existantes — qui deviennent faux au premier rafraîchissement de
l'extrait. L'agent affirmerait alors, avec assurance, qu'un canal existe alors qu'il a
disparu.

La ligne de partage retenue : **tout ce qu'une requête SQL peut établir est généré ici** ;
ce que les données *signifient*, et ce qu'elles ne permettent pas, est écrit à la main dans
les fichiers `.md` voisins — aucune requête ne le dira.

Bénéfice second : le prompt devient vérifiable contre sa source. `tests/test_prompt.py`
contrôle que chaque valeur énoncée existe, et que chaque valeur de la base est énoncée.

**Contrainte de déterminisme.** Ce texte est le préfixe mis en cache, et le cache est une
correspondance d'octets : une seule différence et tout est recalculé au prix fort. Or
``SELECT DISTINCT`` ne garantit aucun ordre. Chaque requête ci-dessous porte donc un
``ORDER BY``, et aucun horodatage n'entre dans le texte produit.
"""

from __future__ import annotations

import duckdb

TABLES = ("media", "kpi_compteurs", "contexte")

# Au-delà de ce nombre de valeurs distinctes, une colonne n'est pas énumérée : le modèle
# la découvre par `SELECT DISTINCT`. Le seuil est un arbitrage de coût — lister les
# les valeurs de `support` coûterait plusieurs centaines de tokens sur chaque question, contre un aller-retour
# d'outil sur les seules questions concernées.
SEUIL_ENUMERATION = 20

# Colonnes de `media` dont les valeurs sont énumérées si elles restent sous le seuil.
# `type` en est volontairement absente : ses nombreuses valeurs mêlent deux formes, et les quatre
# colonnes qui en dérivent la décrivent mieux.
COLONNES_ENUMEREES = (
    "entity", "category", "typology", "channel",
    "performance_metric", "objectif", "format",
)


def _lignes(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def _valeurs(con: duckdb.DuckDBPyConnection, table: str, colonne: str) -> list[str]:
    """Valeurs distinctes, triées — le tri est ce qui rend le prompt cacheable."""
    return [
        str(r[0])
        for r in _lignes(
            con,
            f"SELECT DISTINCT {colonne} FROM {table} "
            f"WHERE {colonne} IS NOT NULL ORDER BY 1",
        )
    ]


def _colonnes(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in _lignes(
            con,
            "SELECT column_name, data_type FROM duckdb_columns() "
            f"WHERE table_name = '{table}' ORDER BY column_index",
        )
    ]


def _bornes(con: duckdb.DuckDBPyConnection, table: str) -> tuple[str, str, int]:
    debut, fin, n = _lignes(
        con,
        f"SELECT MIN(step_date)::DATE, MAX(step_date)::DATE, "
        f"COUNT(DISTINCT step_date) FROM {table}",
    )[0]
    return str(debut), str(fin), int(n)


# --- Sections -------------------------------------------------------------------------


def periode(con: duckdb.DuckDBPyConnection) -> str:
    """Bornes temporelles, par table.

    Elles ne coïncident pas : `media` démarre une semaine avant les deux autres. Annoncer
    une période unique serait faux, et fausserait tout calcul de date relative.
    """
    lignes = ["## Période couverte", ""]
    for table in TABLES:
        debut, fin, n = _bornes(con, table)
        lignes.append(f"- `{table}` : {debut} → {fin} ({n} semaines)")
    lignes += [
        "",
        "Les dates relatives (« les six derniers mois », « le trimestre dernier ») se "
        "calculent depuis la **fin des données**, jamais depuis la date du jour. En SQL : "
        "`(SELECT MAX(step_date) FROM media)`.",
    ]
    return "\n".join(lignes)


def structure(con: duckdb.DuckDBPyConnection) -> str:
    """Colonnes et types des trois tables, avec leur grain et leur volumétrie."""
    grains = {
        "media": "une semaine × un segment média",
        "kpi_compteurs": "une semaine × une énergie",
        "contexte": "une semaine × une variable",
    }
    lignes = ["## Schéma", ""]
    for table in TABLES:
        n = _lignes(con, f"SELECT COUNT(*) FROM {table}")[0][0]
        lignes += [
            f"### {table} — {n} lignes",
            f"Grain : {grains[table]}.",
            "",
            "```",
            *(f"{nom:24s} {typ}" for nom, typ in _colonnes(con, table)),
            "```",
            "",
        ]
    return "\n".join(lignes).rstrip()


def valeurs_possibles(con: duckdb.DuckDBPyConnection) -> str:
    """Colonnes énumérables de `media`, et celles qui ne le sont pas.

    Annoncer explicitement les colonnes non énumérées est aussi utile que d'énumérer les
    autres : sans ça, le modèle ne sait pas s'il regarde une liste complète ou un extrait,
    et peut conclure à l'absence sans vérifier.
    """
    lignes = ["## Valeurs des colonnes de `media`", ""]
    a_decouvrir = []

    for colonne in COLONNES_ENUMEREES:
        valeurs = _valeurs(con, "media", colonne)
        if len(valeurs) > SEUIL_ENUMERATION:
            a_decouvrir.append((colonne, len(valeurs)))
            continue
        lignes.append(f"- `{colonne}` ({len(valeurs)}) : {', '.join(valeurs)}")

    for colonne in ("support", "type"):
        n = _lignes(
            con, f"SELECT COUNT(DISTINCT {colonne}) FROM media"
        )[0][0]
        a_decouvrir.append((colonne, n))

    lignes += [
        "",
        "Ces listes sont **exhaustives** : une valeur qui n'y figure pas n'existe pas dans "
        "les données.",
        "",
        "En revanche, ces colonnes ont trop de valeurs pour être listées ici :",
        "",
        *(f"- `{c}` — {n} valeurs distinctes" for c, n in a_decouvrir),
        "",
        "Avant de conclure qu'une de leurs valeurs n'existe pas, faire un "
        "`SELECT DISTINCT` pour vérifier.",
    ]
    return "\n".join(lignes)


def metriques_de_performance(con: duckdb.DuckDBPyConnection) -> str:
    """Le couple canal → métrique, qui est une propriété structurelle des données."""
    couples = _lignes(
        con,
        "SELECT performance_metric, STRING_AGG(DISTINCT channel, ', ' ORDER BY channel) "
        "FROM media GROUP BY 1 ORDER BY 1",
    )
    lignes = [
        "## Métriques de performance",
        "",
        "Dans `media`, la colonne `performance` porte une valeur dont la nature est donnée "
        "par `performance_metric`. **Chaque canal n'a qu'une seule métrique** :",
        "",
        *(f"- `{metric}` : {canaux}" for metric, canaux in couples),
        "",
        "Ces unités ne sont ni comparables ni additionnables entre elles : un GRP mesure "
        "une couverture d'audience, une impression un affichage, un clic une interaction. "
        "Demander une métrique à un canal qui ne la porte pas n'a pas de réponse.",
    ]
    return "\n".join(lignes)


def perimetres(con: duckdb.DuckDBPyConnection) -> str:
    """La distinction annonceur / marché, et l'homonymie des métriques.

    Section la plus importante du prompt généré : c'est le seul piège du jeu de données
    qui produit une réponse **fausse mais crédible**, les ordres de grandeur étant voisins.
    """
    annonceur = _valeurs(con, "media", "brand_name")
    metriques = _lignes(
        con,
        "SELECT metric, STRING_AGG(DISTINCT brand_name, ', ' ORDER BY brand_name) "
        "FROM contexte GROUP BY 1 ORDER BY 1",
    )
    return "\n".join(
        [
            "## Périmètres — à lire avant toute agrégation",
            "",
            f"`media` et `kpi_compteurs` ne décrivent **qu'un seul annonceur** "
            f"(`brand_name` = {', '.join(f'`{a}`' for a in annonceur)}).",
            "",
            "`contexte` décrit **le marché**, concurrents compris. Ses variables et les "
            "marques pour lesquelles elles existent :",
            "",
            *(f"- `{metric}` : {marques}" for metric, marques in metriques),
            "",
            "**Trois de ces variables portent le même nom que des données des deux autres "
            "tables, avec un périmètre différent et des ordres de grandeur voisins :**",
            "",
            "| Variable | Dans `media` / `kpi_compteurs` | Dans `contexte` |",
            "|---|---|---|",
            "| `cost` | l'annonceur (`media.cost`) | les concurrents |",
            "| `grp` | l'annonceur (`media.performance`) | les concurrents |",
            "| `compteurs` | l'annonceur (`kpi_compteurs`) | les fournisseurs du marché |",
            "",
            "Ne jamais additionner ces colonnes entre tables : elles décrivent des acteurs "
            "différents. Toujours indiquer dans la réponse de quel périmètre il s'agit.",
        ]
    )


def generer(con: duckdb.DuckDBPyConnection) -> str:
    """Assemble la description générée. Déterministe : mêmes données, mêmes octets."""
    return "\n\n".join(
        section(con)
        for section in (
            structure,
            periode,
            perimetres,
            valeurs_possibles,
            metriques_de_performance,
        )
    )
