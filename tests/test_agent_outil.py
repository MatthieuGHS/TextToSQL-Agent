"""Tests de l'outil : sa description, qui est du cache, et son pont vers `sql.py`."""

from __future__ import annotations

import hashlib
import json
import pathlib

import duckdb
import pytest

from src.agent import outil
from src.db import connexion


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    chemin = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute("CREATE TABLE media AS SELECT 'tv' AS channel, 1000.0 AS cost")
    con.close()
    return chemin


@pytest.fixture
def con(base):
    c = connexion.ouvrir(base)
    yield c
    c.close()


# --- La description est du préfixe mis en cache ---------------------------------------

# Empreinte du schéma d'outil. Elle n'a pas de valeur en soi : elle existe pour qu'une
# modification soit **consciente**. Le schéma s'assemble avant le prompt système dans la
# requête, donc il en fait partie ; une reformulation invalide tout le cache sans lever
# d'erreur ni d'avertissement — on ne s'en aperçoit que sur la facture.
EMPREINTE_ATTENDUE = "f984c0baef8c"


def test_le_schema_de_l_outil_est_stable():
    """Si ce test échoue après une modification volontaire de la description : mettre à
    jour la constante, et savoir ce que ça emporte.

    Deux choses, pas une. Le prochain appel repaiera l'écriture du cache de l'API — c'est
    le coût visible. Et **le cache du harnais d'évaluation devient périmé** : la
    description d'outil est prescriptive, elle change ce que le modèle répond. Elle entre
    donc dans `agent_reel.empreinte_reglages`, et une campagne jouée sous l'ancienne
    description ne se compare plus à une campagne jouée sous la nouvelle.

    Ne pas mettre à jour la constante sans mesurer à nouveau : un message d'échec qui dit
    seulement « mettre à jour » invite au geste qui casse la mesure en silence.
    """
    empreinte = hashlib.sha256(
        json.dumps(outil.OUTIL_SQL, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]

    assert empreinte == EMPREINTE_ATTENDUE, (
        f"le schéma de l'outil a changé ({empreinte}) : tout le préfixe mis en cache est "
        f"invalidé, et sera repayé au premier appel."
    )


def test_la_description_dit_quand_appeler_l_outil():
    """Une description qui dit seulement *ce que fait* l'outil laisse le modèle décider
    s'il en a besoin — et il répond alors parfois de mémoire, ce que le prompt interdit."""
    description = outil.OUTIL_SQL["description"]

    assert "Appelle cet outil" in description
    assert outil.OUTIL_SQL["input_schema"]["properties"]["query"]["description"]


# --- Le pont vers sql.py --------------------------------------------------------------


def test_une_requete_valide_renvoie_ses_lignes(con):
    executee = outil.executer("SELECT channel, cost FROM media", con)

    assert executee.a_reussi
    assert executee.colonnes == ["channel", "cost"]
    assert executee.lignes == [("tv", 1000.0)]
    assert executee.tables == ["media"]


def test_une_requete_en_echec_n_annonce_aucune_table(con):
    """Une erreur de syntaxe n'a lu aucune table ; en nommer une serait l'inventer.

    Le tâtonnement est affiché à l'utilisateur comme les autres requêtes : lui montrer
    « tables : media » sur une requête qui n'a jamais atteint la base lui apprendrait
    quelque chose de faux sur ce qui vient de se passer.
    """
    executee = outil.executer("SELECT inconnue FROM media", con)

    assert not executee.a_reussi
    assert executee.tables == []


@pytest.mark.parametrize(
    "query, attendu",
    [
        ("DROP TABLE media", "SELECT"),
        ("SELECT inconnue FROM media", "channel"),
        ("SELCT 1", "non analysable"),
    ],
)
def test_les_echecs_deviennent_un_champ_et_ne_levent_pas(con, query, attendu):
    """Une exception qui remonterait ici arrêterait une boucle qui a encore des essais.

    Le message doit rester exploitable : c'est lui que le modèle lira pour se reprendre.
    """
    executee = outil.executer(query, con)

    assert not executee.a_reussi
    assert attendu in executee.erreur


def test_le_rendu_d_un_echec_est_le_message_d_erreur(con):
    executee = outil.executer("SELECT inconnue FROM media", con)

    assert outil.en_texte(executee) == executee.erreur


def test_le_rendu_d_un_succes_est_un_tableau(con):
    executee = outil.executer("SELECT channel FROM media", con)
    rendu = outil.en_texte(executee)

    assert "channel" in rendu and "tv" in rendu


def test_le_rendu_d_un_resultat_vide_est_une_information(con):
    executee = outil.executer("SELECT * FROM media WHERE channel = 'tiktok'", con)

    assert "vide" in outil.en_texte(executee).lower()
