"""Tests du runner d'évaluation.

L'agent est remplacé par un bouchon : le runner est exerçable de bout en bout avant que
l'agent existe, ce qui est précisément l'ordre imposé par le plan.

On vérifie surtout ce qui rendrait la mesure malhonnête sans se voir : un cache qui ne
se périme pas quand le prompt change, un mode à blanc qui appelle quand même l'API, un
score agrégé qui masque l'instabilité.
"""

from __future__ import annotations

import duckdb
import pytest

from tests.eval import runner as r
from tests.eval.assertions import Resultat, SqlExecutable, TexteContient
from tests.eval.corpus import Cas


@pytest.fixture
def con():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE media AS SELECT 1000.0 AS cost, 'tv' AS channel")
    return con


class AgentBouchon:
    """Agent scriptable. `reponses` est consommée dans l'ordre, la dernière est répétée."""

    def __init__(self, reponses: list[Resultat], identifiant="modele-test",
                 empreinte="abc123", reglages="reg00000"):
        self.reponses = reponses
        self.identifiant = identifiant
        self.empreinte_prompt = empreinte
        self.empreinte_reglages = reglages
        self.appels = 0

    def __call__(self, question: str) -> r.ReponseAgent:
        resultat = self.reponses[min(self.appels, len(self.reponses) - 1)]
        self.appels += 1
        return r.ReponseAgent(
            resultat, {"input_tokens": 100, "output_tokens": 20, "cache_read": 80}
        )


CAS = Cas(
    propriete="propriété de test",
    question="Combien ?",
    assertions=(SqlExecutable(), TexteContient(("total",))),
)


def test_execute_k_fois(con):
    agent = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])

    executions = r.executer((CAS,), agent, con, k=3)

    assert len(executions) == 3
    assert agent.appels == 3
    assert all(e.ok for e in executions)


def test_un_echec_d_assertion_fait_echouer_l_execution(con):
    agent = AgentBouchon(
        [Resultat(reponse="je ne sais pas", sql=["SELECT * FROM inexistante"])]
    )

    executions = r.executer((CAS,), agent, con, k=1)

    assert not executions[0].ok
    assert {v.nom for v in executions[0].echecs} == {
        "SQL exécutable",
        "texte mentionne total…",
    }


def test_le_score_expose_l_instabilite(con):
    """Deux réussites sur trois n'est ni un succès ni un échec : c'est le cas à regarder."""
    agent = AgentBouchon([
        Resultat(reponse="total 1000", sql=["SELECT 1"]),
        Resultat(reponse="aucune idée", sql=[]),          # l'exécution 2 échoue
        Resultat(reponse="total 1000", sql=["SELECT 1"]),
    ])

    scores = r.par_propriete(r.executer((CAS,), agent, con, k=3))

    assert str(scores["propriété de test"]) == "2/3"
    assert scores["propriété de test"].lecture == "instable"


# --- Cache et mode à blanc ------------------------------------------------------------


def test_le_cache_evite_de_rappeler_l_agent(con, tmp_path):
    agent = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])
    cache = r.Cache(tmp_path)

    r.executer((CAS,), agent, con, k=2, cache=cache)
    assert agent.appels == 2

    executions = r.executer((CAS,), agent, con, k=2, cache=cache)
    assert agent.appels == 2, "le second passage n'aurait pas dû rappeler l'agent"
    assert all(e.depuis_le_cache for e in executions)


def test_un_prompt_modifie_perime_le_cache(con, tmp_path):
    """Sans ça, on comparerait les scores de deux prompts sur les mêmes réponses."""
    cache = r.Cache(tmp_path)
    avant = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])],
                         empreinte="prompt-v1")
    r.executer((CAS,), avant, con, k=1, cache=cache)

    apres = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])],
                         empreinte="prompt-v2")
    r.executer((CAS,), apres, con, k=1, cache=cache)

    assert apres.appels == 1, "un prompt modifié doit changer la clé de cache"


def test_un_modele_different_perime_le_cache(con, tmp_path):
    cache = r.Cache(tmp_path)
    r.executer((CAS,), AgentBouchon([Resultat("total 1000", ["SELECT 1"])],
                                    identifiant="modele-a"), con, k=1, cache=cache)
    autre = AgentBouchon([Resultat("total 1000", ["SELECT 1"])], identifiant="modele-b")

    r.executer((CAS,), autre, con, k=1, cache=cache)

    assert autre.appels == 1


def test_un_reglage_different_perime_le_cache(con, tmp_path):
    """Le test qui manquait, et le défaut qu'il aurait attrapé.

    La clé ne portait que le modèle, le prompt, la question et la répétition. L'effort de
    raisonnement et les plafonds de boucle n'y étaient pas — alors que ce sont
    exactement les premières choses qu'on fait varier : le balayage d'effort, puis chaque
    itération de réglage.

    Sans ce contrôle, relancer sous un réglage différent resservait les réponses de
    l'ancien. Trois rapports identiques, et la conclusion « le réglage ne change rien »,
    énoncée avec assurance et fausse. Aucune erreur, aucun avertissement — le pire genre
    de défaut de mesure.
    """
    cache = r.Cache(tmp_path)
    avant = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])],
                         reglages="effort-medium")
    r.executer((CAS,), avant, con, k=1, cache=cache)

    apres = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])],
                         reglages="effort-high")
    r.executer((CAS,), apres, con, k=1, cache=cache)

    assert apres.appels == 1, "un réglage modifié doit changer la clé de cache"


def test_le_rapport_annonce_les_repetitions_par_origine(con):
    """Un k unique serait faux pour l'une des deux origines.

    Le corpus tourne à k=3, la grille à k=1 — elle se note à la main, y répéter les
    questions ne mesure rien. Le rapport annonçait le k du corpus pour tout le monde :
    relu dans trois semaines, il aurait fait croire que la grille avait été jouée trois
    fois. Le compte est désormais lu dans les exécutions, jamais dans les arguments.
    """
    du_corpus = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])
    cas_grille = Cas(propriete="grille/x", question="Q ?", assertions=(), source="grille")

    executions = r.executer((CAS,), du_corpus, con, k=3)
    executions += r.executer((cas_grille,), du_corpus, con, k=1)

    assert r.repetitions_par_source(executions) == {"corpus": 3, "grille": 1}
    assert "corpus 3 · grille 1" in r.rapport(executions, du_corpus)


def test_le_mode_a_blanc_n_appelle_jamais_l_agent(con, tmp_path):
    cache = r.Cache(tmp_path)
    peuple = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])
    r.executer((CAS,), peuple, con, k=2, cache=cache)

    muet = AgentBouchon([Resultat(reponse="jamais utilisé", sql=[])])
    executions = r.executer((CAS,), muet, con, k=2, cache=cache, a_blanc=True)

    assert muet.appels == 0
    assert len(executions) == 2


def test_le_mode_a_blanc_sans_cache_ne_produit_rien(con, tmp_path):
    agent = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])

    executions = r.executer((CAS,), agent, con, k=2, cache=r.Cache(tmp_path), a_blanc=True)

    assert executions == []
    assert agent.appels == 0


# --- Assertions universelles ----------------------------------------------------------


def test_les_assertions_universelles_s_appliquent_partout(con):
    """Un cas qui ne demande rien sur l'arrêt le subit quand même.

    C'est la propriété des assertions universelles : elles ne dépendent d'aucune
    caractéristique du cas. Ici `CAS` n'en parle pas, et pourtant une réponse rendue
    après un abandon de la boucle échoue — sans quoi le harnais compterait un succès
    sur un texte que l'agent n'a pas fini d'écrire.
    """
    agent = AgentBouchon(
        [Resultat(reponse="total 1000", sql=["SELECT 1"], arret="reponse_tronquee")]
    )

    executions = r.executer((CAS,), agent, con, k=1)

    assert not executions[0].ok
    assert {v.nom for v in executions[0].echecs} == {"arrêt normal"}


# --- Rapport --------------------------------------------------------------------------


def test_le_rapport_epingle_le_modele_et_le_prompt(con):
    agent = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])],
                         identifiant="claude-x-9", empreinte="deadbeef")

    texte = r.rapport(r.executer((CAS,), agent, con, k=2), agent)

    assert "claude-x-9" in texte
    assert "deadbeef" in texte
    assert "propriété de test" in texte


def test_le_rapport_signale_les_questions_instables(con):
    agent = AgentBouchon([
        Resultat(reponse="total 1000", sql=["SELECT 1"]),
        Resultat(reponse="aucune idée", sql=[]),
    ])

    texte = r.rapport(r.executer((CAS,), agent, con, k=2), agent)

    assert "instable" in texte.lower()


def test_le_rapport_porte_le_cout(con):
    agent = AgentBouchon([Resultat(reponse="total 1000", sql=["SELECT 1"])])

    texte = r.rapport(r.executer((CAS,), agent, con, k=2), agent)

    assert "Coût" in texte
    assert "taux de lecture de cache" in texte
