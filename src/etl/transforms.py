"""Transformations pures de l'ETL.

Chaque fonction prend un DataFrame et en renvoie un autre. Aucune ne lit de fichier,
n'ouvre de connexion ni n'écrit de journal : elles sont testables sur quelques lignes
inventées, sans base ni CSV.

Le pourquoi de chaque transformation est documenté au fil du code — ces choix de schéma
sont le résultat d'une analyse du jeu de données, pas des conventions arbitraires.
"""

from __future__ import annotations

import pandas as pd

# Séparateur utilisé par la source pour encoder une hiérarchie dans la colonne `type`.
# Exemple : "burst||classique||TF1||30" = objectif || format || support || durée.
TYPE_SEPARATOR = "||"

# Marqueur des lignes de remplissage : la source complète sa grille de dates avec des
# lignes vides pour les canaux tv et video. Elles valent 0 des deux côtés (coût ET
# performance) et ne portent donc aucune information.
PADDING_TYPE = "none||none||none||none"

# Nombre de niveaux dans la hiérarchie encodée.
TYPE_DEPTH = 4

MEDIA_COLUMNS = [
    "step_date", "entity", "category", "typology", "channel",
    "objectif", "format", "support", "duree_sec", "type_raw",
    "cost", "performance", "performance_metric",
]

CONTEXTE_COLUMNS = [
    "step_date", "metric", "brand_name", "entity",
    "category", "typology", "channel", "type", "value",
]

# Les colonnes du KPI portent un préfixe qui identifie le client et n'apporte rien.
KPI_RENAMES = {
    "ref_te_new_counters_without_dem": "new_counters_without_dem",
    "ref_te_dem": "dem",
    "ref_te_inbound": "inbound",
    "ref_te_outbound": "outbound",
    "ref_te_partners": "partners",
    "ref_te_web": "web",
    "ref_te_mes": "mes",
    "ref_te_cdf": "cdf",
}


class ContextColumnError(ValueError):
    """Nom de colonne du fichier de contexte impossible à décoder."""


def split_type_hierarchy(types: pd.Series) -> pd.DataFrame:
    """Éclate la colonne `type` en quatre dimensions exploitables.

    La source encode deux formes différentes dans une même colonne :

    - hiérarchique — ``"burst||classique||TF1||30"`` pour la TV et la vidéo ;
    - plate — ``"brand"``, ``"nonbrand"``, ``"acq"``, ``"rtg"`` pour les autres canaux.

    Les deux atterrissent dans `objectif`. C'est ce qui permet à une requête
    ``WHERE objectif = 'brand'`` de fonctionner sur le SEA comme
    ``WHERE format = 'VOL'`` fonctionne sur la vidéo, sans recourir à un LIKE fragile
    sur les 502 valeurs brutes.

    Returns:
        DataFrame de quatre colonnes (objectif, format, support, duree_sec),
        indexé comme la Series d'entrée.
    """
    parts = types.str.split(TYPE_SEPARATOR, regex=False, expand=True)

    # Si aucune ligne du lot ne contient de séparateur, pandas ne crée qu'une colonne.
    # On complète pour que l'accès aux niveaux 2 à 4 reste valide.
    for level in range(parts.shape[1], TYPE_DEPTH):
        parts[level] = None

    # `parts[1]` non nul signale une valeur hiérarchique : on garde alors le premier
    # niveau. Sinon la valeur était plate et doit être conservée entière.
    is_hierarchical = parts[1].notna()

    return pd.DataFrame(
        {
            "objectif": parts[0].where(is_hierarchical, types),
            "format": parts[1],
            "support": parts[2],
            # Le 4e niveau vaut parfois "Non identifie" : `coerce` le transforme en NaN
            # plutôt que de lever une exception.
            "duree_sec": pd.to_numeric(parts[3], errors="coerce"),
        },
        index=types.index,
    )


def drop_padding_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Retire les lignes de remplissage de la source.

    Ces lignes valent zéro en coût *et* en performance : elles ne décrivent aucune
    activité média. Les conserver aurait trois effets indésirables :

    1. ``'none'`` apparaîtrait dans les valeurs distinctes de `objectif` et `format`,
       donc dans la description du schéma fournie au modèle de langage ;
    2. les moyennes seraient faussées (le coût moyen d'une ligne TV passe de 13 509 €
       à 12 416 €, soit 8 % d'écart, à cause de 366 zéros artificiels) ;
    3. les ``COUNT(*)`` seraient gonflés de 6 %.

    Ce filtrage ne doit pas être confondu avec un nettoyage des anomalies métier : les
    lignes à coût nul mais performance positive (294 lignes) sont, elles, conservées —
    ce sont de vraies anomalies de facturation que l'agent doit pouvoir détecter.

    Returns:
        Le DataFrame filtré, et le nombre de lignes retirées.
    """
    keep = df["type"] != PADDING_TYPE
    return df.loc[keep].copy(), int((~keep).sum())


def build_media(features_cost: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Construit la table `media` à partir de features_cost.

    Format long : une ligne porte une seule métrique de performance, dont la nature est
    donnée par `performance_metric`. Ce choix rend visible dans le schéma le fait que
    GRP, impressions et clics coexistent dans la même colonne et ne sont pas
    additionnables — information qu'un format large (trois colonnes distinctes)
    masquerait.

    Returns:
        La table `media`, et le nombre de lignes de remplissage retirées.
    """
    df, n_padding = drop_padding_rows(features_cost)

    hierarchy = split_type_hierarchy(df["type"])

    media = df.assign(
        step_date=pd.to_datetime(df["step_date"]),
        objectif=hierarchy["objectif"],
        format=hierarchy["format"],
        support=hierarchy["support"],
        duree_sec=hierarchy["duree_sec"],
        # Conservée pour la traçabilité : permet de remonter à la valeur source si un
        # résultat surprend.
        type_raw=df["type"],
    )

    return media[MEDIA_COLUMNS].reset_index(drop=True), n_padding


def build_kpi(compteurs: pd.DataFrame) -> pd.DataFrame:
    """Construit la table `kpi_compteurs`.

    Format large, contrairement à `contexte` : les huit mesures sont peu nombreuses,
    stables et sémantiquement distinctes. Des noms de colonnes explicites rendent le
    schéma auto-descriptif, là où un format long obligerait le modèle à connaître la
    chaîne exacte à mettre dans un ``WHERE metric = ...``.
    """
    kpi = compteurs.assign(step_date=pd.to_datetime(compteurs["step_date"]))
    return kpi.rename(columns=KPI_RENAMES).reset_index(drop=True)


def parse_context_column(column: str) -> tuple[str, str, str, str, str, str, str]:
    """Décode un nom de colonne du fichier de contexte.

    Motif : ``metrique_categorie_typologie_canal_type_marque_entite``.

    La lecture se fait **depuis la droite** parce que la métrique peut elle-même
    contenir des tirets bas (``market_share``, ``taux_switch``, ``fee_filleul``,
    ``ratio_top10_requetes``). Les six derniers segments sont toujours des jetons
    simples : on les prélève d'abord, et tout ce qui reste devant constitue la métrique.

    Raises:
        ContextColumnError: si la colonne compte moins de sept segments.
    """
    segments = column.split("_")
    if len(segments) < 7:
        raise ContextColumnError(
            f"Colonne de contexte non décodable : {column!r} — "
            f"{len(segments)} segments trouvés, 7 minimum attendus "
            f"(metrique_categorie_typologie_canal_type_marque_entite)."
        )

    entity, brand, type_, channel, typology, category = (
        segments[-1], segments[-2], segments[-3],
        segments[-4], segments[-5], segments[-6],
    )
    metric = "_".join(segments[:-6])

    if not metric:
        raise ContextColumnError(
            f"Colonne de contexte sans métrique : {column!r}."
        )

    return metric, category, typology, channel, type_, brand, entity


def build_contexte(features_context: pd.DataFrame) -> pd.DataFrame:
    """Construit la table `contexte` en dépivotant le fichier source.

    La source est en format large : une ligne par semaine, 46 colonnes dont les noms
    encodent les variables. Le format long est préféré ici — à l'inverse du KPI — parce
    que les variables sont nombreuses et varient selon des dimensions (métrique, marque,
    entité) qu'on veut pouvoir filtrer et grouper.

    Comparer le prix d'EDF à celui d'Engie devient un ``GROUP BY brand_name`` générique,
    au lieu d'exiger de nommer deux colonnes précises.
    """
    ctx = features_context.assign(
        step_date=pd.to_datetime(features_context["step_date"])
    )

    long = ctx.melt(id_vars="step_date", var_name="col", value_name="value")

    decoded = pd.DataFrame(
        [parse_context_column(col) for col in long["col"]],
        columns=["metric", "category", "typology", "channel", "type", "brand_name", "entity"],
        index=long.index,
    )

    contexte = pd.concat([long[["step_date", "value"]], decoded], axis=1)
    return contexte[CONTEXTE_COLUMNS].reset_index(drop=True)
