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
# la découvre par `SELECT DISTINCT`. Le seuil est un arbitrage de coût — énumérer une
# colonne à forte cardinalité se paie sur *chaque* question, alors que la découvrir ne se
# paie qu'un aller-retour d'outil, sur les seules questions concernées. La mesure qui a
# fixé ce seuil est consignée hors dépôt, avec les chiffres.
SEUIL_ENUMERATION = 20

# Colonnes de `media` dont les valeurs sont énumérées si elles restent sous le seuil.
COLONNES_ENUMEREES = (
    "entity", "category", "typology", "channel",
    "performance_metric", "objectif", "format",
    "support",
)

# Écartée de l'énumération quelle que soit sa cardinalité, et pour une raison qui n'est
# pas le volume : ses valeurs mêlent deux formes, et les quatre colonnes qui en dérivent
# la décrivent mieux. La distinguer des colonnes simplement trop nombreuses évite
# d'annoncer au modèle un motif faux.
COLONNE_ECARTEE = "type"

# Variables de `contexte` qui portent le même nom qu'une donnée des deux autres tables,
# avec un périmètre différent. C'est le seul piège du jeu de données qui produit une
# réponse fausse *mais crédible*.
#
# Pourquoi écrit et non généré, alors que la règle du module est l'inverse : deux de ces
# trois collisions se dérivent (`cost` est une colonne de `media`, `grp` une valeur de
# `performance_metric`), mais `compteurs` non — le lien passe par le fait qu'une table
# nommée `kpi_compteurs` compte des compteurs, ce qu'aucune requête ne dira. La partie
# dérivable est donc couverte par un test de complétude (`tests/test_prompt.py`) qui
# passe au rouge si un rafraîchissement introduit une quatrième collision.
HOMONYMES = {
    "cost": ("l'annonceur (`media.cost`)", "les concurrents"),
    "grp": ("l'annonceur (`media.performance`)", "les concurrents"),
    "compteurs": ("l'annonceur (`kpi_compteurs`)", "les fournisseurs du marché"),
}


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
    """Colonnes et types des trois tables, avec leur grain et leur volumétrie.

    **Le grain est chiffré autant qu'énoncé**, sur défaut constaté le 25/08/2026 : le
    modèle écrivait `COUNT(*) AS n_lignes`, nommait donc sa colonne correctement, puis
    la racontait comme un nombre de semaines. « Une semaine × un segment média » est
    exact mais demande une déduction qu'il ne fait pas sous charge ; « jusqu'à N lignes
    pour une même semaine » rend l'équation `COUNT(*) = semaines` visiblement absurde.
    """
    grains = {
        "media": "une semaine × un segment média",
        "kpi_compteurs": "une semaine × une énergie",
        "contexte": "une semaine × une variable",
    }
    lignes = ["## Schéma", ""]
    for table in TABLES:
        n, semaines, maxi = _lignes(
            con,
            f"SELECT COUNT(*), COUNT(DISTINCT step_date), "
            f"MAX(n) FROM (SELECT step_date, COUNT(*) AS n FROM {table} "
            f"GROUP BY step_date)",
        )[0]
        lignes += [
            f"### {table} — {n} lignes",
            f"Grain : {grains[table]}. **{n} lignes pour {semaines} semaines "
            f"distinctes**, jusqu'à {maxi} lignes pour une même semaine — compter les "
            f"lignes ne compte donc pas les semaines.",
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

    Le seuil décide seul de ce qui bascule d'un groupe à l'autre — rien n'est classé à la
    main. Écrire « ces colonnes ont trop de valeurs » sur une colonne sans compter ses
    valeurs serait une affirmation que la base ne garantit pas, et qu'un extrait plus
    étroit rendrait fausse en silence : c'est-à-dire exactement le défaut que la
    génération existe pour éviter.
    """
    lignes = ["## Valeurs des colonnes de `media`", ""]
    a_decouvrir = []

    for colonne in COLONNES_ENUMEREES:
        valeurs = _valeurs(con, "media", colonne)
        if len(valeurs) > SEUIL_ENUMERATION:
            a_decouvrir.append((colonne, len(valeurs)))
            continue
        lignes.append(f"- `{colonne}` ({len(valeurs)}) : {', '.join(valeurs)}")

    lignes += [
        "",
        "Ces listes sont **exhaustives** : une valeur qui n'y figure pas n'existe pas dans "
        "les données.",
    ]

    if a_decouvrir:
        lignes += [
            "",
            "En revanche, ces colonnes ont trop de valeurs pour être listées ici :",
            "",
            *(f"- `{c}` — {n} valeurs distinctes" for c, n in a_decouvrir),
        ]

    (n_ecartee,) = _lignes(
        con, f"SELECT COUNT(DISTINCT {COLONNE_ECARTEE}) FROM media"
    )[0]
    lignes += [
        "",
        f"La colonne `{COLONNE_ECARTEE}` ({n_ecartee} valeurs distinctes) n'est pas "
        "listée non plus : elle mêle deux formes, et les colonnes qui en dérivent la "
        "décrivent mieux. Elle reste consultable telle quelle.",
        "",
        "Avant de conclure qu'une valeur de ces colonnes n'existe pas, faire un "
        "`SELECT DISTINCT` pour vérifier.",
    ]
    return "\n".join(lignes)


def metriques_de_performance(con: duckdb.DuckDBPyConnection) -> str:
    """Le couple canal → métrique, qui est une propriété structurelle des données.

    **Porter une métrique et la renseigner sont deux choses**, distinguées ici depuis le
    25/08/2026. Un canal peut être déclaré au schéma sur une métrique dont il n'a pas une
    seule valeur non nulle : vrai au schéma, trompeur en fait. Le modèle le nommait dans
    un total auquel il ne contribue rien, et aurait répondu « 0 » à qui l'interrogeait
    dessus — présenté comme une mesure et non comme une absence.

    Entièrement déduit de la base, aucune valeur écrite à la main : la mention disparaît
    d'elle-même le jour où un rafraîchissement peuple la colonne.
    """
    couples = _lignes(
        con,
        "SELECT performance_metric, STRING_AGG(DISTINCT channel, ', ' ORDER BY channel) "
        "FROM media GROUP BY 1 ORDER BY 1",
    )
    creux = [
        canal
        for (canal,) in _lignes(
            con,
            "SELECT channel FROM media GROUP BY channel "
            "HAVING COALESCE(MAX(performance), 0) = 0 ORDER BY channel",
        )
    ]
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
    if creux:
        lignes += [
            "",
            "**Porter une métrique n'est pas la renseigner.** Ces canaux sont déclarés "
            "sur leur métrique mais n'en ont aucune valeur non nulle sur toute la "
            f"période : {', '.join(f'`{c}`' for c in creux)}. Leur total vaut zéro par "
            "absence de mesure, pas par mesure d'une absence — ne pas les citer parmi "
            "les contributeurs d'un total, et dire que la donnée manque plutôt que de "
            "rendre « 0 » comme un résultat.",
        ]
    return "\n".join(lignes)


def activite(con: duckdb.DuckDBPyConnection) -> str:
    """Comment l'inactivité est encodée, canal par canal — présent n'est pas actif.

    Section née d'un défaut constaté le 25/08/2026 : à « le canal vidéo a-t-il un
    historique complet ? », trois exécutions ont écrit trois filtres différents
    (`cost > 0`, `performance IS NOT NULL`, aucun) et rendu trois verdicts
    contradictoires. Le choix du filtre était un tirage au sort, parce que rien ne disait
    au modèle ce qu'une semaine sans diffusion **ressemble** dans cette base.

    Or elle y ressemble à une ligne présente valant zéro, et non à une ligne absente :
    la plupart des canaux ont une ligne pour chaque semaine de la période. Les trois
    colonnes ci-dessous rendent la différence entre « présent » et « actif » lisible
    d'un coup d'œil, sans qu'aucun seuil ni aucune interprétation ne soit écrit à la
    main — tout se recalcule à chaque construction.
    """
    lignes = _lignes(
        con,
        "SELECT channel, COUNT(DISTINCT step_date), "
        "COUNT(DISTINCT step_date) FILTER (WHERE cost > 0), "
        "COUNT(DISTINCT step_date) FILTER (WHERE performance > 0), "
        "COUNT(*) FILTER (WHERE cost IS NULL) "
        "FROM media GROUP BY channel ORDER BY channel",
    )
    return "\n".join(
        [
            "## Présence et activité — « présent » n'est pas « actif »",
            "",
            "Une semaine sans diffusion est le plus souvent une **ligne présente valant "
            "zéro**, pas une ligne absente. Compter les lignes d'un canal ne dit donc "
            "rien de son activité réelle : il faut filtrer sur la mesure.",
            "",
            "| Canal | Semaines présentes | dont `cost > 0` | dont `performance > 0` |",
            "|---|---|---|---|",
            *(
                f"| `{canal}` | {presentes} | "
                f"{'— (`cost` NULL)' if nuls else avec_cout} | {avec_perf} |"
                for canal, presentes, avec_cout, avec_perf, nuls in lignes
            ),
            "",
            "Lire ce tableau avant de choisir un filtre : `cost = 0` signifie « aucune "
            "diffusion enregistrée cette semaine-là », tandis que `cost IS NULL` "
            "signifie « sans objet » — un canal non acheté n'a pas de dépense à zéro, il "
            "n'a pas de dépense du tout. Un canal dont les semaines présentes dépassent "
            "largement les semaines actives est intermittent, pas incomplet.",
        ]
    )


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
            f"**{len(HOMONYMES)} de ces variables portent le même nom que des données "
            "des deux autres tables, avec un périmètre différent et des ordres de "
            "grandeur voisins :**",
            "",
            "| Variable | Dans `media` / `kpi_compteurs` | Dans `contexte` |",
            "|---|---|---|",
            *(
                f"| `{nom}` | {ici} | {la} |"
                for nom, (ici, la) in sorted(HOMONYMES.items())
            ),
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
            activite,
        )
    )
