"""Tests de l'adaptateur agent → harnais. Toujours aucun appel API.

C'est l'endroit où le dispositif de mesure touche enfin le modèle, donc l'endroit où
l'invariant « la suite de tests ne coûte rien » est le plus facile à perdre. Il tient
parce que l'`Agent` d'E4 est injecté : le faux modèle de `test_agent_boucle.py` suffit.

Ce qui se joue ici n'est pas de la plomberie. Trois décisions décident de ce que le score
voudra dire — ce qui compte comme requête, ce qui n'est pas un échec de l'agent, et sur
quel modèle la mesure est épinglée.
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest
from langchain_core.messages import AIMessage, SystemMessage

from src.agent import boucle, outil
from src.agent.reponse import Arret
from tests.eval import agent_reel
from tests.eval.assertions import ArretNormal, SqlExecutable
from tests.test_agent_boucle import ModeleScripte, appel_sql, coupe, texte
from src.db import connexion

IDENTIFIANT = "claude-sonnet-5"


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


def adaptateur(modele, con, identifiant: str = IDENTIFIANT) -> agent_reel.AgentReel:
    return agent_reel.AgentReel(
        agent=boucle.Agent(
            modele=modele,
            systeme=SystemMessage(content="prompt d'essai"),
            empreinte_prompt="essai00000ab",
            con=con,
        ),
        identifiant=identifiant,
    )


# --- 1. Ce qui compte comme requête ---------------------------------------------------


def test_les_requetes_en_echec_ne_sont_pas_transmises(con):
    """Le tâtonnement est un comportement voulu, pas une faute.

    E4 conserve les requêtes en échec exprès, pour distinguer « juste du premier coup » de
    « juste après trois essais ». Mais les transmettre aux assertions ferait échouer
    `SqlExecutable` sur une auto-correction réussie — le harnais pénaliserait alors ce que
    la boucle a été écrite pour permettre.
    """
    modele = ModeleScripte(
        [
            appel_sql("SELECT inconnue FROM media", "t1"),
            appel_sql("SELECT cost FROM media", "t2"),
            texte("Le total est de 1 000 €."),
        ]
    )

    reponse = adaptateur(modele, con)("combien ?")

    assert reponse.resultat.sql == ["SELECT cost FROM media"]
    assert reponse.usage["tatonnements"] == 1
    assert SqlExecutable().verifier(reponse.resultat, con).ok, (
        "une auto-correction réussie doit passer l'assertion d'exécutabilité"
    )


def test_le_motif_d_arret_voyage_jusqu_aux_assertions(con):
    """Sans lui, une réponse coupée passerait pour aboutie : le texte ne le dit pas."""
    modele = ModeleScripte([coupe("Le budget s'élève à 1 0")])

    reponse = adaptateur(modele, con)("quel budget ?")

    assert reponse.resultat.arret == Arret.REPONSE_TRONQUEE.value
    assert not ArretNormal().verifier(reponse.resultat, con).ok


def test_une_reponse_sans_requete_reste_transmise(con):
    """Demander une précision est un arrêt normal : l'adaptateur ne doit pas le trahir."""
    modele = ModeleScripte([texte("De quelles ventes parlez-vous ?")])

    reponse = adaptateur(modele, con)("combien de ventes ?")

    assert reponse.resultat.sql == []
    assert ArretNormal().verifier(reponse.resultat, con).ok


# --- 2. Ce qui n'est pas un échec de l'agent ------------------------------------------


def test_une_panne_d_api_leve_au_lieu_d_etre_notee(con):
    """Une panne de réseau n'est pas un défaut de l'agent.

    La noter ferait varier le score avec la météo de l'infrastructure, et deux campagnes
    prises à deux moments ne seraient plus comparables. L'exception laisse au runner le
    soin d'écarter l'exécution et de la rapporter à part.
    """

    class ModeleEnPanne:
        def invoke(self, messages):
            import anthropic

            raise anthropic.APIConnectionError(request=None)

    with pytest.raises(agent_reel.ErreurApi):
        adaptateur(ModeleEnPanne(), con)("combien ?")


# --- 3. Sur quel modèle la mesure est épinglée ----------------------------------------


def test_un_changement_de_modele_arrete_la_campagne(con):
    """`claude-sonnet-5` est un alias : il désignera une autre génération un jour.

    Sans ce contrôle, le rapport mélangerait deux modèles sans le dire — et le cache,
    indexé sur l'identifiant, resservirait des réponses produites par l'autre. C'est le
    genre de mesure qu'on croit comparable et qui ne l'est pas.
    """
    modele = ModeleScripte(
        [
            AIMessage(
                content="Réponse.",
                response_metadata={"model": "claude-sonnet-6", "stop_reason": "end_turn"},
            )
        ]
    )

    with pytest.raises(agent_reel.ModeleInattendu, match="claude-sonnet-6"):
        adaptateur(modele, con, identifiant="claude-sonnet-5")("question ?")


def test_l_empreinte_du_prompt_est_relayee(con):
    """Le cache du harnais s'en sert comme clé : un prompt modifié doit le périmer."""
    adapte = adaptateur(ModeleScripte([texte("Réponse.")]), con)

    assert adapte.empreinte_prompt == "essai00000ab"


# --- 4. L'usage, dans les clés que le runner lit --------------------------------------


def test_l_usage_est_dans_les_cles_attendues_par_le_runner(con):
    """Renommer une clé ici ferait afficher zéro au rapport, sans erreur."""
    modele = ModeleScripte(
        [
            texte(
                "Fini.",
                usage={
                    "input_tokens": 3000,
                    "output_tokens": 120,
                    "total_tokens": 3120,
                    "input_token_details": {
                        "cache_read": 2700,
                        "cache_creation": 0,
                        "ephemeral_5m_input_tokens": 200,
                    },
                },
            )
        ]
    )

    usage = adaptateur(modele, con)("combien ?").usage

    assert usage["input_tokens"] == 3000
    assert usage["output_tokens"] == 120
    assert usage["cache_read"] == 2700
    assert usage["cache_creation"] == 200
    assert usage["tatonnements"] == 0
