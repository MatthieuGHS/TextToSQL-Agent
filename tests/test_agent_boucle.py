"""Tests de la boucle agent — sans aucun appel API.

C'est la contrainte qui a décidé de l'architecture : le client de modèle est injecté, donc
un faux modèle rejouant des réponses écrites à la main suffit à couvrir toute la boucle.
Sans ça, ces tests coûteraient de l'argent et ne tourneraient pas à chaque sauvegarde.

Ce qu'ils couvrent, dans cet ordre d'importance :

1. **Les bornes** — ce sont elles qui font la différence entre une intention de prompt et
   une garantie. Chacune est vue en train de mordre.
2. **La reprise sur erreur** — les messages d'E2 sont rédigés pour être lus par le modèle ;
   encore faut-il qu'ils lui parviennent.
3. **Les arrêts anormaux** — refus, panne d'API : aucun ne doit produire de trace
   d'exécution ni de réponse qui ressemble à un succès.
"""

from __future__ import annotations

import pathlib

import anthropic
import duckdb
import pytest
from langchain_core.messages import AIMessage

from src.agent import boucle, outil
from src.agent.boucle import Agent, ask
from src.agent.reponse import Arret, Echange
from src.db import connexion


# --- Le faux modèle -------------------------------------------------------------------


class ModeleScripte:
    """Rejoue des réponses préparées, et retient ce qu'on lui a envoyé.

    Retenir les messages est la moitié de l'intérêt : c'est ainsi qu'on vérifie que le
    résultat d'outil — et surtout un message d'erreur — est bien reparti vers le modèle.
    """

    def __init__(self, reponses: list[AIMessage]):
        self.reponses = list(reponses)
        self.appels: list[list] = []

    def invoke(self, messages):
        self.appels.append(list(messages))
        if not self.reponses:
            raise AssertionError("le modèle a été appelé plus de fois que prévu")
        return self.reponses.pop(0)


class ModeleEnPanne:
    def __init__(self, exception: Exception):
        self.exception = exception

    def invoke(self, messages):
        raise self.exception


def texte(contenu: str, usage: dict | None = None) -> AIMessage:
    return AIMessage(
        content=contenu,
        response_metadata={"model": "claude-sonnet-5", "stop_reason": "end_turn"},
        usage_metadata=usage,
    )


def appel_sql(query: str, identifiant: str = "t1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": outil.NOM, "args": {"query": query}, "id": identifiant}],
        response_metadata={"model": "claude-sonnet-5", "stop_reason": "tool_use"},
    )


# --- La base d'essai ------------------------------------------------------------------


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    chemin = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'tv', 1000.0), (DATE '2024-09-09', 'sea', 250.0)"
        ") t(step_date, channel, cost)"
    )
    con.close()
    return chemin


@pytest.fixture
def con(base):
    c = connexion.ouvrir(base)
    yield c
    c.close()


def agent_avec(modele, con, **reglages) -> Agent:
    from langchain_core.messages import SystemMessage

    return Agent(
        modele=modele,
        systeme=SystemMessage(content="prompt d'essai"),
        empreinte_prompt="essai00000ab",
        con=con,
        **reglages,
    )


# --- 1. Les bornes --------------------------------------------------------------------


def test_plafond_d_iterations_mord(con):
    """Un modèle qui redemande une requête sans jamais conclure doit être arrêté.

    Sans ce plafond, rien dans le code n'empêche une boucle infinie : le prompt ne peut
    que le suggérer, et une suggestion n'est pas une garantie.
    """
    modele = ModeleScripte([appel_sql("SELECT 1", f"t{i}") for i in range(10)])

    reponse = ask("et ensuite ?", agent=agent_avec(modele, con, max_iterations=3))

    assert reponse.arret is Arret.PLAFOND_ITERATIONS
    assert not reponse.arret.est_normal
    assert len(modele.appels) == 3, "la boucle a dépassé son plafond"
    assert len(reponse.requetes) == 3, "le travail déjà fait doit rester dans la réponse"


def test_echecs_sql_repetes_arretent_la_boucle(con):
    """Distinct du plafond d'itérations : le modèle tourne en rond sur une erreur.

    L'arrêter plus tôt évite de payer les itérations restantes pour rien.
    """
    modele = ModeleScripte(
        [appel_sql("SELECT inconnue FROM media", f"t{i}") for i in range(6)]
    )

    agent = agent_avec(modele, con, max_iterations=6, max_echecs_sql=2)
    reponse = ask("combien ?", agent=agent)

    assert reponse.arret is Arret.TROP_D_ECHECS_SQL
    assert len(reponse.requetes) == 2
    assert len(modele.appels) == 2, "la boucle a continué après le plafond d'échecs"


def test_un_succes_remet_le_compteur_d_echecs_a_zero(con):
    """Le plafond porte sur des échecs *consécutifs*.

    Un modèle qui se trompe, se reprend, puis se retrompe travaille normalement — le
    compter comme trois échecs de suite l'interromprait en plein travail.
    """
    modele = ModeleScripte(
        [
            appel_sql("SELECT inconnue FROM media", "t1"),
            appel_sql("SELECT COUNT(*) FROM media", "t2"),
            appel_sql("SELECT encore_inconnue FROM media", "t3"),
            texte("Voici le compte."),
        ]
    )

    agent = agent_avec(modele, con, max_iterations=6, max_echecs_sql=2)
    reponse = ask("combien ?", agent=agent)

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert [r.a_reussi for r in reponse.requetes] == [False, True, False]


def test_l_historique_est_borne(con):
    """Sans borne, chaque question porterait le poids de toutes les précédentes."""
    modele = ModeleScripte([texte("Bien reçu.")])
    historique = [Echange(f"question {i}", f"réponse {i}") for i in range(12)]

    ask("et maintenant ?", historique, agent=agent_avec(modele, con))

    envoyes = modele.appels[0]
    # 1 système + 2 messages par échange conservé + la question courante
    assert len(envoyes) == 1 + 2 * boucle.HISTORIQUE_MAX + 1
    assert "question 11" in str(envoyes), "les échanges conservés sont les derniers"
    assert "question 0" not in str(envoyes)


# --- 2. La reprise sur erreur ---------------------------------------------------------


def test_le_message_d_erreur_repart_vers_le_modele(con):
    """Tout le soin mis dans les messages d'E2 ne sert que si le modèle les reçoit.

    On vérifie sur le contenu : le message doit nommer les colonnes réelles, ce qui est
    précisément ce qui permet au modèle de se reprendre au tour suivant.
    """
    modele = ModeleScripte(
        [appel_sql("SELECT spend FROM media"), texte("Le total est de 1 250 €.")]
    )

    reponse = ask("quelles dépenses ?", agent=agent_avec(modele, con))

    recu = str(modele.appels[1])
    assert "cost" in recu and "channel" in recu, "les colonnes réelles sont transmises"
    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.requetes[0].erreur is not None


def test_le_resultat_de_la_requete_repart_vers_le_modele(con):
    modele = ModeleScripte(
        [appel_sql("SELECT channel, cost FROM media"), texte("Deux canaux.")]
    )

    ask("quels canaux ?", agent=agent_avec(modele, con))

    recu = str(modele.appels[1])
    assert "tv" in recu and "1000.0" in recu


def test_une_requete_refusee_ne_leve_pas(con):
    """Un refus d'E2 est une erreur récupérable, pas une panne de la boucle."""
    modele = ModeleScripte([appel_sql("DROP TABLE media"), texte("Je ne peux pas.")])

    reponse = ask("supprime tout", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert "SELECT" in reponse.requetes[0].erreur


def test_un_outil_inconnu_est_signale_au_modele(con):
    modele = ModeleScripte(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "efface_tout", "args": {}, "id": "t1"}],
                response_metadata={"stop_reason": "tool_use"},
            ),
            texte("Compris."),
        ]
    )

    ask("essaie", agent=agent_avec(modele, con))

    assert outil.NOM in str(modele.appels[1])


# --- 3. Les arrêts anormaux -----------------------------------------------------------


def test_un_refus_ne_fait_pas_lire_un_contenu_vide(con):
    """Sur un refus, le contenu est vide : le lire sans vérifier produit un plantage."""
    modele = ModeleScripte(
        [AIMessage(content=[], response_metadata={"stop_reason": "refusal"})]
    )

    reponse = ask("question hors sujet", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REFUS_MODELE
    assert reponse.texte


def test_une_panne_d_api_devient_un_etat_de_la_reponse(con):
    """Aucune trace d'exécution ne sort de `ask()` : c'est l'interface qui décide de ce
    qu'un utilisateur voit, et elle ne peut pas décider sur une exception."""
    panne = anthropic.APIConnectionError(request=None)

    reponse = ask("combien ?", agent=agent_avec(ModeleEnPanne(panne), con))

    assert reponse.arret is Arret.ERREUR_API
    assert reponse.texte


# --- 4. Le chemin nominal, et ce qu'il rapporte ---------------------------------------


def test_chemin_nominal(con):
    modele = ModeleScripte(
        [appel_sql("SELECT SUM(cost) FROM media"), texte("Le total est de 1 250 €.")]
    )

    reponse = ask("quel total ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.arret.est_normal
    assert reponse.texte == "Le total est de 1 250 €."
    assert reponse.a_interroge_la_base
    assert reponse.requetes[0].lignes == [(1250.0,)]


def test_une_reponse_sans_requete_est_un_succes(con):
    """Le prompt invite le modèle à demander une précision quand la question est ambiguë.

    Une boucle qui exigerait au moins une requête casserait ce comportement voulu, et le
    harnais compterait un échec là où l'agent a bien travaillé.
    """
    modele = ModeleScripte([texte("De quelles ventes parlez-vous : MES, CDF ou DEM ?")])

    reponse = ask("combien de ventes ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.arret.est_normal
    assert not reponse.a_interroge_la_base


def test_la_reponse_porte_de_quoi_la_comparer(con):
    """Sans l'identifiant exact du modèle et l'empreinte du prompt, deux mesures prises
    à deux dates ne sont pas comparables — et le harnais ne le saurait pas."""
    modele = ModeleScripte([texte("Réponse.")])

    reponse = ask("question ?", agent=agent_avec(modele, con))

    assert reponse.modele == "claude-sonnet-5"
    assert reponse.empreinte_prompt == "essai00000ab"


def test_les_tokens_se_cumulent_sur_tous_les_appels(con):
    """Le coût d'une question est celui de la boucle entière, pas du dernier appel."""
    modele = ModeleScripte(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": outil.NOM, "args": {"query": "SELECT 1"}, "id": "t1"}
                ],
                response_metadata={"stop_reason": "tool_use"},
                usage_metadata={
                    "input_tokens": 3000,
                    "output_tokens": 50,
                    "total_tokens": 3050,
                    "input_token_details": {
                        "cache_read": 0,
                        "ephemeral_5m_input_tokens": 2700,
                    },
                },
            ),
            texte(
                "Fini.",
                usage={
                    "input_tokens": 3100,
                    "output_tokens": 80,
                    "total_tokens": 3180,
                    "input_token_details": {"cache_read": 2700, "cache_creation": 0},
                },
            ),
        ]
    )

    reponse = ask("combien ?", agent=agent_avec(modele, con))

    assert reponse.usage.entree == 6100
    assert reponse.usage.sortie == 130
    assert reponse.usage.cache_lu == 2700
    # Le connecteur remet `cache_creation` à zéro dès qu'il publie le détail par durée de
    # vie : lire la seule clé générique ferait conclure qu'aucune écriture n'a eu lieu.
    assert reponse.usage.cache_ecrit == 2700
