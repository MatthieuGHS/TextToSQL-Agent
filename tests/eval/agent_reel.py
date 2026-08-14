"""L'agent réel, vu par le harnais.

Le seul module du dispositif de mesure qui sache qu'un modèle existe. Tout le reste —
corpus, assertions, runner — travaille sur un `Resultat`, sans savoir d'où il vient. C'est
ce qui a permis de construire et d'exercer l'instrument entièrement avant l'agent.

Ce fichier fait donc une seule chose : traduire une `AgentResponse` en ce que le harnais
sait lire. Trois décisions y sont prises, et elles pèsent sur ce que le score voudra dire.
"""

from __future__ import annotations

from dataclasses import dataclass

import hashlib
import json
import pathlib

from src.agent import boucle, prompt
from src.agent.reponse import AgentResponse, Arret
from tests.eval.assertions import Resultat

# Levée ici, attrapée par le runner : c'est la seule exception qu'un agent a le droit de
# lever. Elle vit chez lui parce qu'elle fait partie du contrat, pas de l'adaptateur.
from tests.eval.runner import ErreurApi, ReponseAgent

__all__ = [
    "AgentReel", "AgentHorsLigne", "ErreurApi", "ModeleInattendu",
    "construire", "empreinte_reglages", "hors_ligne",
]


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

    @property
    def empreinte_reglages(self) -> str:
        return empreinte_reglages(self.agent, self.effort)

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


def empreinte_reglages(agent: boucle.Agent, effort: str) -> str:
    """Hachage de tout ce qui, hors modèle et prompt, change la réponse produite.

    Deux règles pour décider de son contenu. **Y mettre ce qui peut faire répondre
    autrement** : l'effort et le raisonnement changent la génération, le plafond de sortie
    décide des troncatures, les plafonds de boucle décident du nombre d'allers-retours.
    **N'y mettre que ça** : y ajouter un réglage sans effet ferait repayer une campagne
    entière pour rien.

    Trié, donc reproductible d'un processus à l'autre — c'est la condition pour qu'une
    clé de cache tienne entre deux exécutions du programme.
    """
    reglages = {
        "effort": effort,
        "raisonnement": boucle.RAISONNEMENT,
        "max_tokens": boucle.MAX_TOKENS,
        "max_iterations": agent.max_iterations,
        "max_echecs_sql": agent.max_echecs_sql,
    }
    brut = json.dumps(reglages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:12]


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
    empreinte_reglages: str
    effort: str = ""

    def __call__(self, question: str) -> ReponseAgent:
        raise RuntimeError(
            "mode à blanc : l'agent a été appelé, ce qui ne devrait jamais arriver."
        )


# Registre des campagnes réelles, **une entrée par jeu de réglages**.
#
# L'empreinte du prompt se recalcule hors ligne depuis la base ; l'identifiant exact du
# modèle et l'effort de raisonnement, non — le premier ne descend que d'une réponse, le
# second n'a laissé aucune trace dans le cache. Les deux figurent au rapport parce que deux
# mesures prises sous des réglages différents ne se comparent pas.
#
# Un registre et non une trace unique : le balayage d'effort enchaîne plusieurs campagnes,
# et écraser la précédente rendrait la ligne de base irrejouable à blanc alors que ses
# réponses sont toujours en cache. Ce qu'on veut comparer, on doit pouvoir le relire.
FICHIER_MODELE = "modele.json"


def construire(racine: pathlib.Path, effort: str = boucle.EFFORT) -> AgentReel:
    """Assemble l'agent réel et épingle l'identifiant avant toute exécution.

    L'ordre compte : la sonde d'abord, la campagne ensuite. C'est ce qui garantit que la
    toute première réponse mise en cache l'est déjà sous le bon identifiant, plutôt que
    sous l'alias — auquel cas un changement de génération la resservirait en silence.
    """
    agent = boucle.construire(effort=effort)
    identifiant = boucle.identifiant_exact(agent)
    empreinte = empreinte_reglages(agent, effort)

    racine.mkdir(parents=True, exist_ok=True)
    registre = _lire_registre(racine)
    registre["campagnes"][empreinte] = {"identifiant": identifiant, "effort": effort}
    registre["derniere"] = empreinte
    (racine / FICHIER_MODELE).write_text(
        json.dumps(registre, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return AgentReel(agent=agent, identifiant=identifiant, effort=effort)


def _lire_registre(racine: pathlib.Path) -> dict:
    fichier = racine / FICHIER_MODELE
    if not fichier.exists():
        return {"campagnes": {}, "derniere": None}
    return json.loads(fichier.read_text(encoding="utf-8"))


def hors_ligne(
    racine: pathlib.Path, con, cible: str | None = None
) -> AgentHorsLigne:
    """Reconstitue les clés du cache sans un seul appel.

    L'empreinte du prompt est recalculée depuis la base — E3 garantit qu'elle est
    déterministe, c'est même ce qui rend le cache possible. Si le prompt a bougé depuis la
    campagne, l'empreinte diffère et le cache ne répond plus : le rapport le dira, ce qui
    est le comportement voulu plutôt qu'une comparaison entre deux prompts différents.

    Args:
        cible: quelle campagne rejouer — un **effort** ou une **empreinte de réglages**.
            Par défaut la dernière. Deux formes pour un seul argument parce qu'elles
            répondent au même besoin à deux moments : l'effort suffit tant qu'il désigne
            une campagne unique, l'empreinte est le recours quand il n'en désigne plus
            une seule. Un second argument n'aurait fait qu'ajouter la question « lequel
            gagne s'ils se contredisent ? ».
    """
    fichier = racine / FICHIER_MODELE
    if not fichier.exists():
        raise FileNotFoundError(
            f"aucune campagne réelle n'a encore eu lieu ({fichier} absent) : le mode à "
            f"blanc n'a rien à rejouer."
        )
    registre = _lire_registre(racine)
    campagnes = registre["campagnes"]

    if cible is None:
        empreinte = registre["derniere"]
    elif cible in campagnes:
        empreinte = cible
    else:
        # Toutes les campagnes de cet effort, pas la première trouvée. L'effort ne
        # suffira plus à en désigner une dès E8, qui fera varier `MAX_ITERATIONS` à
        # effort constant : deux entrées `medium`, et un `next()` en rendrait une au
        # hasard de l'ordre d'insertion. Rejouer à blanc la mauvaise campagne ne lève
        # rien et produit un rapport plausible — même famille que les deux défauts de
        # cache déjà corrigés. On lève plutôt que de choisir.
        candidates = [e for e, c in campagnes.items() if c["effort"] == cible]
        if not candidates:
            connus = sorted({c["effort"] for c in campagnes.values()})
            raise KeyError(
                f"aucune campagne à l'effort {cible!r} ; efforts disponibles : {connus}"
            )
        if len(candidates) > 1:
            raise KeyError(
                f"{len(candidates)} campagnes à l'effort {cible!r} : "
                f"{sorted(candidates)}. L'effort ne les distingue pas — relancer en "
                f"passant l'empreinte de réglages voulue à la place."
            )
        empreinte = candidates[0]

    campagne = campagnes[empreinte]
    return AgentHorsLigne(
        identifiant=campagne["identifiant"],
        empreinte_prompt=prompt.empreinte(prompt.construire(con)),
        # Relue et non recalculée : les réglages de la campagne ne sont plus en mémoire,
        # et les recalculer depuis les constantes actuelles ferait pointer vers un cache
        # qui n'existe pas dès que l'une d'elles a bougé. Le registre est la seule source.
        empreinte_reglages=empreinte,
        effort=campagne.get("effort", ""),
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
