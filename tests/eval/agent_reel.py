"""L'agent réel, vu par le harnais.

Le seul module du dispositif de mesure qui sache qu'un modèle existe. Tout le reste —
corpus, assertions, runner — travaille sur un `Resultat`, sans savoir d'où il vient. C'est
ce qui a permis de construire et d'exercer l'instrument entièrement avant l'agent.

Ce fichier fait donc une seule chose : traduire une `AgentResponse` en ce que le harnais
sait lire. Trois décisions y sont prises, et elles pèsent sur ce que le score voudra dire.
"""

from __future__ import annotations

from dataclasses import dataclass

import json
import pathlib

from src.agent import boucle, prompt
from src.agent.reponse import AgentResponse, Arret
from tests.eval.assertions import Resultat

# Levée ici, attrapée par le runner : c'est la seule exception qu'un agent a le droit de
# lever. Elle vit chez lui parce qu'elle fait partie du contrat, pas de l'adaptateur.
from tests.eval.runner import ErreurApi, ReponseAgent

__all__ = ["AgentReel", "ErreurApi", "ModeleInattendu", "construire", "hors_ligne"]


class ModeleInattendu(Exception):
    """L'identifiant du modèle a changé en cours de campagne.

    Arrêt immédiat plutôt qu'un rapport qui mélangerait deux modèles sans le dire — c'est
    exactement le genre de mesure qu'on croirait comparable et qui ne l'est pas.
    """


@dataclass
class AgentReel:
    """Adaptateur `ask()` → protocole du harnais.

    L'`Agent` d'E4 est **injecté** plutôt que construit ici. Deux bénéfices : cet
    adaptateur se teste avec un faux modèle, sans consommer un appel ; et l'appelant garde
    la main sur l'effort de raisonnement, qui est une variable de mesure.
    """

    agent: boucle.Agent
    identifiant: str
    effort: str = boucle.EFFORT

    @property
    def empreinte_prompt(self) -> str:
        return self.agent.empreinte_prompt

    def __call__(self, question: str) -> ReponseAgent:
        reponse = boucle.ask(question, agent=self.agent)

        if reponse.arret is Arret.ERREUR_API:
            raise ErreurApi(f"appel en échec sur : {question[:80]}")

        if reponse.modele != self.identifiant:
            raise ModeleInattendu(
                f"campagne épinglée sur {self.identifiant}, réponse produite par "
                f"{reponse.modele}. Les mesures ne seraient plus comparables."
            )

        return ReponseAgent(resultat=_resultat(reponse), usage=_usage(reponse))


@dataclass(frozen=True)
class AgentHorsLigne:
    """De quoi retrouver le cache, sans pouvoir appeler quoi que ce soit.

    Le mode à blanc a besoin des deux clés d'indexation — identifiant du modèle et
    empreinte du prompt — mais d'aucun modèle. Lui en donner un serait une porte ouverte :
    une exécution absente du cache partirait payer un appel, et le mode à blanc ne serait
    plus à blanc.

    D'où cet objet qui satisfait le protocole et **lève si on l'appelle**. Le mode à blanc
    n'est alors plus une intention, c'est une propriété : s'il appelait, on le saurait.
    """

    identifiant: str
    empreinte_prompt: str
    effort: str = ""

    def __call__(self, question: str) -> ReponseAgent:
        raise RuntimeError(
            "mode à blanc : l'agent a été appelé, ce qui ne devrait jamais arriver."
        )


# Trace de la dernière campagne réelle. L'empreinte du prompt se recalcule hors ligne
# depuis la base ; l'identifiant exact du modèle et l'effort de raisonnement, non — le
# premier ne descend que d'une réponse, le second n'a laissé aucune trace dans le cache.
# Les deux figurent au rapport parce que deux mesures prises sous des réglages différents
# ne se comparent pas : un rapport à blanc qui les perdrait ne serait plus opposable.
FICHIER_MODELE = "modele.json"


def construire(racine: pathlib.Path, effort: str = boucle.EFFORT) -> AgentReel:
    """Assemble l'agent réel et épingle l'identifiant avant toute exécution.

    L'ordre compte : la sonde d'abord, la campagne ensuite. C'est ce qui garantit que la
    toute première réponse mise en cache l'est déjà sous le bon identifiant, plutôt que
    sous l'alias — auquel cas un changement de génération la resservirait en silence.
    """
    agent = boucle.construire(effort=effort)
    identifiant = boucle.identifiant_exact(agent)

    racine.mkdir(parents=True, exist_ok=True)
    (racine / FICHIER_MODELE).write_text(
        json.dumps(
            {"identifiant": identifiant, "effort": effort}, ensure_ascii=False, indent=2
        )
    )

    return AgentReel(agent=agent, identifiant=identifiant, effort=effort)


def hors_ligne(racine: pathlib.Path, con) -> AgentHorsLigne:
    """Reconstitue les clés du cache sans un seul appel.

    L'empreinte du prompt est recalculée depuis la base — E3 garantit qu'elle est
    déterministe, c'est même ce qui rend le cache possible. Si le prompt a bougé depuis la
    campagne, l'empreinte diffère et le cache ne répond plus : le rapport le dira, ce qui
    est le comportement voulu plutôt qu'une comparaison entre deux prompts différents.
    """
    fichier = racine / FICHIER_MODELE
    if not fichier.exists():
        raise FileNotFoundError(
            f"aucune campagne réelle n'a encore eu lieu ({fichier} absent) : le mode à "
            f"blanc n'a rien à rejouer."
        )
    trace = json.loads(fichier.read_text())
    return AgentHorsLigne(
        identifiant=trace["identifiant"],
        empreinte_prompt=prompt.empreinte(prompt.construire(con)),
        effort=trace.get("effort", ""),
    )


def _resultat(reponse: AgentResponse) -> Resultat:
    """Ce que les assertions verront.

    **Seules les requêtes réussies sont transmises.** Une requête refusée ou fautive est
    un tâtonnement, et E4 conserve les tâtonnements exprès. Les passer à `SqlExecutable`
    ferait échouer une auto-correction — c'est-à-dire pénaliser le comportement pour
    lequel la boucle a été écrite. Leur nombre part dans l'usage, où il devient une
    mesure de qualité au lieu d'un échec.
    """
    return Resultat(
        reponse=reponse.texte,
        sql=[r.sql for r in reponse.requetes if r.a_reussi],
        graphique=False,  # E7 le renseignera
        arret=reponse.arret.value,
    )


def _usage(reponse: AgentResponse) -> dict:
    """Les clés sont celles que `runner.cout()` lit — ne pas les renommer sans lui.

    `input_tokens` est un **total**, cache compris : c'est la convention du connecteur,
    et `boucle._usage_de` s'en tient déjà à elle. La mélanger avec celle de l'API brute,
    où le même nom désigne le reste non caché, fausserait tout calcul de coût.
    """
    return {
        "input_tokens": reponse.usage.entree,
        "output_tokens": reponse.usage.sortie,
        "cache_read": reponse.usage.cache_lu,
        "cache_creation": reponse.usage.cache_ecrit,
        "tatonnements": sum(1 for r in reponse.requetes if not r.a_reussi),
    }
