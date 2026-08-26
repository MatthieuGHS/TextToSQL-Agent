"""Tests de l'adaptateur agent → harnais. Toujours aucun appel API.

C'est l'endroit où le dispositif de mesure touche enfin le modèle, donc l'endroit où
l'invariant « la suite de tests ne coûte rien » est le plus facile à perdre. Il tient
parce que l'`Agent` d'E4 est injecté : le faux modèle de `test_agent_boucle.py` suffit.

Ce qui se joue ici n'est pas de la plomberie. Trois décisions décident de ce que le score
voudra dire — ce qui compte comme requête, ce qui n'est pas un échec de l'agent, et sur
quel modèle la mesure est épinglée.
"""

from __future__ import annotations

import json
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
    # Deux lignes et non une : `src/charts` refuse — à juste titre — de tracer un résultat
    # d'une seule ligne, qui ne fait ni évolution ni comparaison. Une base d'essai à une
    # ligne rendait ce refus indistinguable d'un champ jamais renseigné.
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "('tv', 1000.0), ('sea', 250.0)) t(channel, cost)"
    )
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


# --- 4. Ce que la clé de réglages doit contenir ---------------------------------------
#
# Trois défauts de cache déjà corrigés ont la même forme : une clé d'indexation qui ne
# contient pas tout ce qui distingue ce qu'elle indexe. Le symptôme est toujours le même —
# une campagne resservie sous d'autres réglages, un rapport plausible, aucune erreur.
# Ces tests posent la contre-épreuve : faire varier un réglage, et exiger que l'empreinte
# bouge.

AGENT_NU = boucle.Agent(modele=None, systeme=None, empreinte_prompt="")

# Empreinte des réglages **courants** à l'effort `medium`, épinglée. Elle n'a pas de
# valeur en soi : elle existe pour qu'une modification soit **consciente**, comme
# `EMPREINTE_ATTENDUE` pour le schéma d'outil. Chaque changement porte sa date et son
# motif — c'est ce qui distingue une empreinte qu'on a décidé de faire bouger d'une
# empreinte qui a bougé sans qu'on le voie.
#
# 11/08/2026 · 4f7a1f99217d — campagne de référence, celle de la ligne de base.
# 19/08/2026 · f03a91e8d24d — `MAX_ITERATIONS` 4 → 5, sur défaut constaté deux fois.
#   Mesuré le 24/08 (campagne B0) : corpus 48/51 contre 47/51, `grain distinct` de 2/3
#   instable à 3/3, coût par question inchangé. Réglage conservé.
# 25/08/2026 · 7b0ab29b6987 — champ `raisonnement` sur l'outil, qui entre dans la clé par
#   `OUTIL_SQL`. Campagne 1 d'E8 : quatre modifications de prompt et celle-ci en une seule
#   péremption du cache.
# 25/08/2026 · 4014d5aee332 — les six textes de repli et `VERSION_BOUCLE` entrent dans la
#   clé. Les textes **sont** `Resultat.reponse` sur tout arrêt anormal, donc ce que lisent
#   `TexteContient` et `TracabiliteNumerique` ; le flux de contrôle, lui, n'était indexé
#   par rien — et le tour de rédaction du plafond change la réponse rendue sans toucher
#   une seule constante. Péremption **voulue** : les 378 exécutions en cache ont été
#   produites par une boucle qui jetait le travail du dernier tour, et les resservir
#   ferait conclure « ça ne change rien » sans erreur ni avertissement.
# 26/08/2026 · 11b906927aa5 — `VERSION_BOUCLE` 2 → 3 : `run_sql` totalise les colonnes
#   issues d'un `SUM()` et `en_texte` rend cette somme au modèle. **Cinquième occurrence
#   de la famille**, et la seule que `docs/decisions.md` avait vue venir : le retour
#   d'outil change à chaque requête sans qu'aucun prompt ni aucune constante de réglage
#   ne bouge. Péremption **voulue** — les 105 exécutions de la campagne du 26/08 ont été
#   produites par un outil qui ne rendait pas la somme, et c'est précisément le
#   comportement dont on veut mesurer l'effet.
EMPREINTE_MEDIUM = "11b906927aa5"


def test_l_empreinte_des_reglages_courants_est_epinglee():
    """Si ce test échoue, les campagnes en cache ne se rejouent plus sous ces réglages.

    Ce n'est pas un défaut à corriger en mettant à jour la constante sans réfléchir : une
    empreinte qui bouge veut dire qu'un réglage a changé, donc que les réponses en cache
    ont été produites sous une autre configuration. Trois issues, et une seule est
    gratuite — annuler le changement de réglage, ou repayer la campagne, ou assumer que
    les deux configurations coexistent. Re-cléer le cache n'est légitime que si le
    réglage n'a pas *réellement* changé depuis les campagnes, ce qui se vérifie dans
    l'historique et pas au jugé.

    La troisième issue est celle prise le 19/08/2026, et elle ne coûte rien parce que le
    registre indexe les campagnes **par empreinte** : la ligne de base reste lisible sous
    la sienne (`--campagne 4f7a1f99217d`), et la campagne à 5 itérations viendra s'ajouter
    à côté. Ce qui serait faux, c'est de comparer les deux sans le dire.
    """
    assert agent_reel.empreinte_reglages(AGENT_NU, "medium") == EMPREINTE_MEDIUM


@pytest.mark.parametrize(
    "constante, valeur",
    [
        ("LIMITE_LIGNES", 50),
        ("BUDGET_CARACTERES", 2000),
        ("DELAI_SECONDES", 3.0),
    ],
)
def test_les_bornes_de_run_sql_changent_l_empreinte(monkeypatch, constante, valeur):
    """Elles décident de ce que le modèle voit d'un résultat, donc de sa réponse.

    Elles ne figuraient dans aucune clé. Baisser `LIMITE_LIGNES` et relancer à blanc
    resservait les réponses obtenues sur 200 lignes, et le rapport concluait « ça ne
    change rien » — la mesure exacte que le balayage de E8 est censé produire.
    """
    avant = agent_reel.empreinte_reglages(AGENT_NU, "medium")
    monkeypatch.setattr(agent_reel.acces_sql, constante, valeur)

    assert agent_reel.empreinte_reglages(AGENT_NU, "medium") != avant


def test_la_description_d_outil_change_l_empreinte(monkeypatch):
    """Elle est prescriptive, et n'entre pas dans l'empreinte du prompt.

    `bind_tools()` l'assemble à part ; `prompt.construire()` ne hache que les `.md` et la
    partie générée. Une description reformulée changeait donc le taux de sollicitation de
    l'outil sans qu'aucune clé ne bouge.
    """
    avant = agent_reel.empreinte_reglages(AGENT_NU, "medium")
    reformule = dict(outil.OUTIL_SQL, description=outil.OUTIL_SQL["description"] + " ")
    monkeypatch.setattr(agent_reel.outil, "OUTIL_SQL", reformule)

    assert agent_reel.empreinte_reglages(AGENT_NU, "medium") != avant


def test_les_plafonds_de_boucle_changent_l_empreinte():
    """Contre-épreuve de la règle inverse : n'y mettre *que* ce qui change la réponse.

    Ces deux-là y étaient déjà. Le test les garde parce qu'une refonte de l'empreinte les
    perdrait sans bruit — et ils décident du nombre d'allers-retours, donc du contenu.
    """
    from dataclasses import replace

    avant = agent_reel.empreinte_reglages(AGENT_NU, "medium")

    assert agent_reel.empreinte_reglages(replace(AGENT_NU, max_iterations=8), "medium") \
        != avant
    assert agent_reel.empreinte_reglages(replace(AGENT_NU, max_echecs_sql=5), "medium") \
        != avant


def test_l_effort_change_l_empreinte():
    """La raison d'être de l'empreinte : le balayage compare trois campagnes."""
    empreintes = {
        agent_reel.empreinte_reglages(AGENT_NU, e) for e in ("low", "medium", "high")
    }

    assert len(empreintes) == 3


# --- 4 bis. Le registre des campagnes -------------------------------------------------


@pytest.fixture
def sans_prompt(monkeypatch):
    """`hors_ligne` recalcule l'empreinte du prompt depuis la base ; pas le sujet ici.

    La base d'essai de ce module ne porte que `media`, quand le prompt décrit les trois
    tables. Neutraliser cette lecture isole ce qui est mesuré — la résolution d'une
    campagne — plutôt que de monter un schéma complet pour un champ dont ces tests ne
    disent rien.
    """
    monkeypatch.setattr(agent_reel.prompt, "construire", lambda con: "prompt d'essai")


def test_deux_modeles_au_meme_effort_ne_se_recouvrent_pas(tmp_path, con, sans_prompt):
    """Défaut constaté le 24/08/2026, avant qu'il n'ait produit un chiffre faux.

    La clé du registre était l'empreinte de réglages seule, et celle-ci ne contient pas
    le modèle — délibérément, le cache le portant déjà de son côté. Deux campagnes au
    même effort sur deux modèles se recouvraient donc : la seconde effaçait la première,
    dont les entrées de cache restaient sur le disque **sans être adressables**, et un
    rejeu à blanc resservait l'autre modèle en silence.

    Ce que la comparaison Sonnet/Opus demandée par le client aurait produit : un rapport
    parfaitement plausible sur la campagne qu'on croyait relire.
    """
    agent_reel.enregistrer(tmp_path, "modele-a", "medium", "reglages-x")
    agent_reel.enregistrer(tmp_path, "modele-b", "medium", "reglages-x")

    campagnes = agent_reel._lire_registre(tmp_path)["campagnes"]

    assert len(campagnes) == 2, "une campagne en a écrasé une autre"
    assert agent_reel.hors_ligne(tmp_path, con, "modele-a").identifiant == "modele-a"
    assert agent_reel.hors_ligne(tmp_path, con, "modele-b").identifiant == "modele-b"
    # Les réglages restent ceux de la campagne : c'est eux qui adressent le cache, et la
    # clé composite ne doit pas fuiter jusque-là.
    assert agent_reel.hors_ligne(tmp_path, con, "modele-a").empreinte_reglages \
        == "reglages-x"


def test_une_cible_ambigue_leve_au_lieu_de_choisir(tmp_path, con, sans_prompt):
    """On lève plutôt que de trancher : le mauvais choix ne lèverait rien, lui.

    Trois formes désignent une campagne — effort, réglages, modèle — et aucune n'est
    garantie unique. Celle qui ne l'est pas doit s'arrêter en disant quoi passer à la
    place, jamais rendre la première venue de l'ordre d'insertion.
    """
    agent_reel.enregistrer(tmp_path, "modele-a", "medium", "reglages-x")
    agent_reel.enregistrer(tmp_path, "modele-b", "medium", "reglages-x")

    with pytest.raises(KeyError, match="exactement une"):
        agent_reel.hors_ligne(tmp_path, con, "reglages-x")
    with pytest.raises(KeyError, match="exactement une"):
        agent_reel.hors_ligne(tmp_path, con, "medium")
    with pytest.raises(KeyError, match="exactement une"):
        agent_reel.hors_ligne(tmp_path, con, "campagne-inconnue")


def test_une_cible_non_ambigue_designe_toujours_sa_campagne(tmp_path, con, sans_prompt):
    """Contre-épreuve de la précédente : la levée ne doit pas être devenue systématique.

    Sans elle, remplacer la résolution par un `raise` inconditionnel passerait le test
    d'ambiguïté — et rendrait le mode à blanc inutilisable.
    """
    agent_reel.enregistrer(tmp_path, "modele-a", "medium", "reglages-x")
    agent_reel.enregistrer(tmp_path, "modele-b", "high", "reglages-y")

    for cible in ("modele-a", "reglages-x", "medium"):
        assert agent_reel.hors_ligne(tmp_path, con, cible).identifiant == "modele-a"
    # Sans cible, la dernière campagne écrite.
    assert agent_reel.hors_ligne(tmp_path, con).identifiant == "modele-b"


# --- 5. Le champ « graphique », enfin renseigné ---------------------------------------


def test_le_graphique_remonte_jusqu_aux_assertions(con):
    """`PasDeGraphiqueSurResultatVide` était inerte depuis sa création.

    `_resultat()` forçait `graphique=False`, faute d'agent capable de dessiner : le
    contrôle rendait `True` sans rien examiner. Il lit désormais `AgentResponse.graphique`,
    ce qui le rend capable d'échouer — c'est-à-dire de mesurer quelque chose.
    """
    modele = ModeleScripte([
        appel_sql("SELECT channel, cost FROM media"),
        texte("Voici la répartition."),
    ])

    resultat = adaptateur(modele, con)("Répartition ?").resultat

    assert resultat.graphique is True


def test_un_resultat_non_tracable_laisse_le_champ_a_faux(con):
    """Contre-épreuve : sans elle, le champ pourrait être vrai en permanence."""
    modele = ModeleScripte([
        appel_sql("SELECT SUM(cost) FROM media"),
        texte("Le total est de 1 000 €."),
    ])

    resultat = adaptateur(modele, con)("Total ?").resultat

    assert resultat.graphique is False

