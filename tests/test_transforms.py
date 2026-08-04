"""Tests des transformations de l'ETL.

Les fonctions testées ici sont pures : elles ne lisent aucun fichier et n'ouvrent
aucune base. Les jeux de données sont inventés et minimaux — quelques lignes suffisent
à couvrir chaque cas, et les tests s'exécutent en quelques millisecondes.

Chaque test documente le comportement métier attendu, pas seulement le code.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.etl import transforms


def media_source(**overrides) -> pd.DataFrame:
    """Construit un features_cost.csv minimal, avec surcharge possible des colonnes."""
    base = {
        "step_date": ["2024-09-02", "2024-09-02"],
        "brand_name": ["te", "te"],
        "entity": ["pge", "pge"],
        "category": ["paid", "paid"],
        "typology": ["offline", "online"],
        "channel": ["tv", "sea"],
        "type": ["burst||classique||TF1||30", "brand"],
        "cost": [1000.0, 500.0],
        "performance": [2.5, 800.0],
        "performance_metric": ["grp", "clicks"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# --- Éclatement de la colonne `type` ------------------------------------------------


def test_split_type_hierarchique():
    """Une valeur à quatre niveaux se répartit sur les quatre colonnes."""
    result = transforms.split_type_hierarchy(pd.Series(["burst||classique||TF1||30"]))

    assert result.loc[0, "objectif"] == "burst"
    assert result.loc[0, "format"] == "classique"
    assert result.loc[0, "support"] == "TF1"
    assert result.loc[0, "duree_sec"] == 30


def test_split_type_plat_conserve_la_valeur_entiere():
    """Une valeur sans séparateur atterrit entière dans `objectif`.

    C'est ce qui permet à `WHERE objectif = 'brand'` de fonctionner sur le SEA. Sans ce
    comportement, seul le premier segment serait conservé et les canaux non
    hiérarchiques perdraient leur information.
    """
    result = transforms.split_type_hierarchy(pd.Series(["brand", "nonbrand", "rtg"]))

    assert list(result["objectif"]) == ["brand", "nonbrand", "rtg"]
    assert result["format"].isna().all()
    assert result["support"].isna().all()
    assert result["duree_sec"].isna().all()


def test_split_type_duree_non_numerique():
    """Une durée non convertible devient NaN au lieu de lever une exception."""
    result = transforms.split_type_hierarchy(
        pd.Series(["acq_other||VOL||Teads||Non identifie"])
    )

    assert result.loc[0, "support"] == "Teads"
    assert pd.isna(result.loc[0, "duree_sec"])


def test_split_type_lot_sans_aucune_hierarchie():
    """Un lot ne contenant que des valeurs plates produit quand même quatre colonnes.

    Sans complétion, pandas ne créerait qu'une colonne et l'accès aux niveaux
    supérieurs échouerait.
    """
    result = transforms.split_type_hierarchy(pd.Series(["acq", "rtg"]))

    assert list(result.columns) == ["objectif", "format", "support", "duree_sec"]
    assert len(result) == 2


def test_split_type_preserve_index():
    """L'index d'origine est conservé, pour que l'affectation reste alignée."""
    types = pd.Series(["brand", "nonbrand"], index=[17, 42])
    result = transforms.split_type_hierarchy(types)

    assert list(result.index) == [17, 42]


# --- Lignes de remplissage ----------------------------------------------------------


def test_drop_padding_rows():
    """Les lignes de remplissage sont retirées, les autres conservées."""
    df = media_source(
        type=[transforms.PADDING_TYPE, "brand"],
        cost=[0.0, 500.0],
        performance=[0.0, 800.0],
    )

    kept, n_removed = transforms.drop_padding_rows(df)

    assert n_removed == 1
    assert len(kept) == 1
    assert kept.iloc[0]["type"] == "brand"


def test_padding_absent_du_resultat_final():
    """Après construction, aucun marqueur 'none' ne subsiste dans les dimensions.

    C'est ce qui garantit que `SELECT DISTINCT objectif` — dont le résultat servira à
    décrire le schéma au modèle de langage — ne contient pas de valeur factice.
    """
    df = media_source(type=[transforms.PADDING_TYPE, "brand"])

    media, n_padding = transforms.build_media(df)

    assert n_padding == 1
    assert "none" not in set(media["objectif"])
    assert "none" not in set(media["format"].dropna())


# --- Table media --------------------------------------------------------------------


def test_build_media_colonnes_et_ordre():
    """La table produite a exactement les colonnes attendues, dans l'ordre."""
    media, _ = transforms.build_media(media_source())

    assert list(media.columns) == transforms.MEDIA_COLUMNS


def test_build_media_conserve_type_raw():
    """La valeur d'origine est conservée pour permettre l'audit des transformations."""
    media, _ = transforms.build_media(media_source())

    assert media.loc[0, "type_raw"] == "burst||classique||TF1||30"


def test_build_media_convertit_les_dates():
    """`step_date` devient un vrai type date, sans quoi YEAR() et QUARTER() échouent."""
    media, _ = transforms.build_media(media_source())

    assert pd.api.types.is_datetime64_any_dtype(media["step_date"])


# --- Table kpi_compteurs ------------------------------------------------------------


def test_build_kpi_retire_le_prefixe_client():
    """Les colonnes sont renommées ; celles hors dictionnaire sont conservées."""
    source = pd.DataFrame(
        {
            "step_date": ["2024-09-02"],
            "energy": ["elec"],
            "ref_te_new_counters_without_dem": [1000],
            "ref_te_dem": [200],
            "ref_te_mes": [800],
            "ref_te_cdf": [400],
        }
    )

    kpi = transforms.build_kpi(source)

    assert "new_counters_without_dem" in kpi.columns
    assert "ref_te_new_counters_without_dem" not in kpi.columns
    assert "energy" in kpi.columns  # non renommée, mais conservée


# --- Décodage des colonnes de contexte ----------------------------------------------


def test_parse_context_metrique_simple():
    """Un nom de colonne standard se décode en sept composants."""
    metric, category, typology, channel, type_, brand, entity = (
        transforms.parse_context_column("price_other_offer_elec_elec_edf_edf")
    )

    assert metric == "price"
    assert (category, typology, channel, type_) == ("other", "offer", "elec", "elec")
    assert (brand, entity) == ("edf", "edf")


@pytest.mark.parametrize(
    "column, expected_metric",
    [
        ("market_share_other_other_elec_elec_te_pge", "market_share"),
        ("taux_switch_other_other_elec_elec_other_other", "taux_switch"),
        ("fee_filleul_other_other_parrainage_parrainage_te_pge", "fee_filleul"),
        ("ratio_top10_requetes_owned_online_seo_seo_te_pge", "ratio_top10_requetes"),
    ],
)
def test_parse_context_metrique_avec_underscores(column, expected_metric):
    """Une métrique contenant des tirets bas est reconstituée entièrement.

    C'est la raison pour laquelle le décodage se fait depuis la droite : compter depuis
    la gauche donnerait `market` au lieu de `market_share`.
    """
    metric, *_ = transforms.parse_context_column(column)

    assert metric == expected_metric


def test_parse_context_colonne_invalide():
    """Un nom malformé lève une erreur nommant la colonne fautive."""
    with pytest.raises(transforms.ContextColumnError, match="price_edf"):
        transforms.parse_context_column("price_edf")


# --- Table contexte -----------------------------------------------------------------


def test_build_contexte_depivote():
    """Une ligne large de N variables devient N lignes longues."""
    source = pd.DataFrame(
        {
            "step_date": ["2025-06-30", "2025-07-07"],
            "price_other_offer_elec_elec_edf_edf": [1300.40, 1305.73],
            "market_share_other_other_elec_elec_te_pge": [0.1043, 0.1039],
        }
    )

    contexte = transforms.build_contexte(source)

    assert len(contexte) == 4  # 2 semaines x 2 variables
    assert list(contexte.columns) == transforms.CONTEXTE_COLUMNS
    assert set(contexte["metric"]) == {"price", "market_share"}


def test_build_contexte_associe_la_bonne_valeur():
    """Le dépivotage n'intervertit pas les valeurs entre variables."""
    source = pd.DataFrame(
        {
            "step_date": ["2025-06-30"],
            "price_other_offer_elec_elec_edf_edf": [1300.40],
            "market_share_other_other_elec_elec_te_pge": [0.1043],
        }
    )

    contexte = transforms.build_contexte(source)
    prix = contexte[contexte["metric"] == "price"].iloc[0]

    assert prix["value"] == pytest.approx(1300.40)
    assert prix["brand_name"] == "edf"
