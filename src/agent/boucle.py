"""La boucle : question → SQL → résultat → réponse.

C'est ici que le code déterministe et le modèle stochastique se rencontrent, et le partage
est le principe directeur du module :

> **Ce qui doit être vrai à chaque fois appartient à la boucle, pas au prompt.**

Le prompt *demande* au modèle de ne pas dépasser son périmètre ; la boucle l'*empêche* de
dépasser un nombre d'appels. La première formulation est une intention, la seconde une
garantie. Tout ce qui pouvait basculer du prompt vers le code a basculé : les plafonds, les
refus, la troncature, la journalisation.

**Le client de modèle est injecté.** C'est ce qui permet à la suite de tests de couvrir
toute la boucle sans consommer un seul appel API — un faux modèle rejoue des réponses
écrites à la main. Cette contrainte a décidé de l'architecture, pas l'inverse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import anthropic
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent import outil, prompt
from src.agent.reponse import AgentResponse, Arret, Echange, RequeteExecutee, Usage
from src.db import connexion

logger = logging.getLogger(__name__)

# --- Configuration : rien d'implicite ---------------------------------------------------
#
# Les trois réglages ci-dessous ne peuvent pas être laissés au défaut. Aucun ne lève
# d'erreur ; les trois coûtent de l'argent ou de la qualité en silence.

MODELE = "claude-sonnet-5"
MAX_TOKENS = 4096

# Le raisonnement est **actif par défaut** sur ce modèle : l'omettre ne le désactive pas,
# ça le laisse au défaut de l'API. On l'écrit donc, et on règle sa profondeur par `EFFORT`.
#
# Ne pas être tenté de le désactiver pour économiser : raisonnement coupé, le modèle
# sollicite *moins* ses outils. Pour un agent dont la seule voie d'accès aux données est un
# outil, c'est le pire compromis — on économise des tokens et on récolte des réponses de
# mémoire. `EFFORT` est un point de départ à mesurer sur le corpus, en regardant le taux
# d'appels d'outil autant que l'exactitude.
RAISONNEMENT = {"type": "adaptive"}
EFFORT = "medium"

# Il n'y a plus de `temperature` sur ce modèle : une valeur non par défaut est refusée.
# Le déterminisme ne peut donc pas venir du modèle — il ne peut venir que du code. C'est
# exactement ce que E2 verrouille, et ce n'est plus un choix de style.

MAX_ITERATIONS = 4
MAX_ECHECS_SQL = 3
HISTORIQUE_MAX = 5

# Textes de repli, écrits ici et non par le modèle : ce sont les seuls cas où la boucle
# parle à sa place, et elle ne dit alors qu'une chose — qu'elle n'a pas abouti.
TEXTE_ERREUR_API = (
    "Le service de modèle est indisponible pour le moment. La question n'a pas pu être "
    "traitée ; réessayer dans un instant."
)
TEXTE_REFUS = (
    "Cette question n'a pas pu être traitée par le modèle. La reformuler, ou la découper "
    "en questions plus précises sur les données."
)
TEXTE_ECHECS_SQL = (
    "Je n'ai pas réussi à écrire une requête valide pour cette question, après plusieurs "
    "tentatives. Elle porte peut-être sur une donnée absente du jeu, ou demande une "
    "précision — reformuler en nommant la table ou la période visée."
)
TEXTE_PLAFOND = (
    "Je n'ai pas abouti dans le nombre d'étapes imparti. Les requêtes déjà exécutées "
    "figurent dans la réponse ; découper la question en deux permettrait d'aboutir."
)


class Modele(Protocol):
    """Le strict minimum attendu d'un client de modèle.

    Volontairement réduit à une méthode : c'est tout ce que la boucle utilise, donc tout
    ce qu'un faux modèle doit imiter. Un protocole plus large ne rendrait pas la boucle
    plus sûre, seulement les tests plus lourds.
    """

    def invoke(self, messages: list[Any]) -> AIMessage: ...


@dataclass(frozen=True)
class Agent:
    """Tout ce qu'une question a besoin de trouver déjà prêt.

    Assemblé **une fois**. Reconstruire le prompt à chaque question le regénérerait pour
    rien et rouvrirait le risque d'instabilité du préfixe — c'est-à-dire la perte du cache,
    qui est la moitié de l'économie du projet.
    """

    modele: Modele
    systeme: SystemMessage
    empreinte_prompt: str
    con: Any = None
    identifiant: str = MODELE
    max_iterations: int = MAX_ITERATIONS
    max_echecs_sql: int = MAX_ECHECS_SQL


def construire(con: Any = None, *, effort: str = EFFORT) -> Agent:
    """Assemble l'agent réel. Ouvre la base et construit le prompt.

    La connexion est partagée par tous les appels : `interrupt()` porte sur la connexion
    et non sur la requête, donc un seul appel peut être en vol à la fois. Suffisant pour
    une interface mono-utilisateur ; à revoir avant tout usage concurrent.

    Args:
        effort: profondeur de raisonnement. Paramétrable parce que c'est une valeur qui
            se mesure et non qui se décrète — le harnais d'évaluation la balaiera.
    """
    # Explicite, et non hérité de `connexion.ouvrir()` : le connecteur lit la clé dans
    # l'environnement, et faire dépendre ça de l'ordre des deux lignes suivantes serait
    # un couplage invisible — qui casserait le jour où un appelant fournit sa connexion.
    load_dotenv(connexion.ROOT / ".env")

    con = con if con is not None else connexion.ouvrir()
    texte = prompt.construire(con)

    modele = ChatAnthropic(
        model=MODELE,
        max_tokens=MAX_TOKENS,
        thinking=RAISONNEMENT,
        reasoning_effort=effort,
    ).bind_tools([outil.OUTIL_SQL])

    return Agent(
        modele=modele,
        # La forme « liste de blocs » n'est pas décorative : c'est la seule qui accepte
        # un marqueur de cache. Posé sur le dernier bloc système, il met en cache les
        # définitions d'outils *et* le prompt, les outils étant assemblés avant.
        systeme=SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": texte,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        ),
        empreinte_prompt=prompt.empreinte(texte),
        con=con,
    )


_defaut: Agent | None = None


def agent_par_defaut() -> Agent:
    global _defaut
    if _defaut is None:
        _defaut = construire()
    return _defaut


def ask(
    question: str,
    historique: Sequence[Echange] = (),
    *,
    agent: Agent | None = None,
) -> AgentResponse:
    """Répond à une question sur les données.

    Une fonction et non une classe : l'état de la conversation est passé, jamais détenu.
    Deux appels ne peuvent donc pas se marcher dessus, et l'interface reste libre de
    stocker l'historique où elle veut.

    Args:
        historique: les échanges précédents ; seuls les derniers sont transmis. Ils sont
            placés *après* le point de coupe du cache, donc leur rotation n'invalide rien.
        agent: pour les tests, ou pour un appelant qui veut maîtriser l'assemblage.
    """
    agent = agent if agent is not None else agent_par_defaut()

    messages: list[Any] = [agent.systeme]
    for echange in list(historique)[-HISTORIQUE_MAX:]:
        messages.append(HumanMessage(echange.question))
        messages.append(AIMessage(echange.reponse))
    messages.append(HumanMessage(question))

    requetes: list[RequeteExecutee] = []
    usage = Usage()
    identifiant = agent.identifiant
    echecs_consecutifs = 0
    dernier_texte = ""

    for _ in range(agent.max_iterations):
        try:
            reponse = agent.modele.invoke(messages)
        except anthropic.APIError as exc:
            # Le connecteur a déjà réessayé les erreurs qui le méritaient. Au-delà, ce
            # n'est plus récupérable ici : ça devient un état de la réponse, pas une
            # trace d'exécution que l'interface aurait à interpréter.
            logger.warning("appel modèle en échec : %s", type(exc).__name__)
            return _finir(agent, TEXTE_ERREUR_API, requetes, Arret.ERREUR_API, usage,
                          identifiant)

        usage = usage + _usage_de(reponse)
        identifiant = reponse.response_metadata.get("model") or identifiant
        dernier_texte = reponse.text or dernier_texte

        # À vérifier **avant** de lire le contenu : sur un refus, il est vide.
        if reponse.response_metadata.get("stop_reason") == "refusal":
            return _finir(agent, TEXTE_REFUS, requetes, Arret.REFUS_MODELE, usage,
                          identifiant)

        if not reponse.tool_calls:
            # Aucune requête n'est un arrêt normal : le prompt invite le modèle à
            # demander une précision quand la question est ambiguë. Une boucle qui
            # exigerait au moins une requête casserait ce comportement voulu.
            return _finir(agent, dernier_texte, requetes, Arret.REPONSE_DONNEE, usage,
                          identifiant)

        messages.append(reponse)
        for appel in reponse.tool_calls:
            executee = _executer_appel(appel, agent.con)
            requetes.append(executee)
            echecs_consecutifs = 0 if executee.a_reussi else echecs_consecutifs + 1
            messages.append(
                ToolMessage(
                    content=outil.en_texte(executee),
                    tool_call_id=appel["id"],
                    status="error" if executee.erreur else "success",
                )
            )

        if echecs_consecutifs >= agent.max_echecs_sql:
            return _finir(agent, TEXTE_ECHECS_SQL, requetes, Arret.TROP_D_ECHECS_SQL,
                          usage, identifiant)

    return _finir(agent, TEXTE_PLAFOND, requetes, Arret.PLAFOND_ITERATIONS, usage,
                  identifiant)


def _executer_appel(appel: dict, con: Any) -> RequeteExecutee:
    """Un seul outil existe ; tout autre nom est une erreur rendue au modèle."""
    if appel.get("name") != outil.NOM:
        return RequeteExecutee(
            sql="",
            erreur=f"Outil inconnu : {appel.get('name')!r}. Le seul outil disponible "
            f"est {outil.NOM}.",
        )
    return outil.executer(appel.get("args", {}).get("query", ""), con)


def _usage_de(message: AIMessage) -> Usage:
    """Lit la consommation, en contournant deux pièges de comptage mesurés.

    1. `input_tokens` du connecteur est un **total** (base + cache lu + cache écrit),
       là où le champ homonyme de l'API brute est le reste non caché. Les mélanger
       fausse tout calcul de coût.
    2. Le connecteur remet `cache_creation` à zéro dès qu'il publie le détail par durée
       de vie, pour éviter un double comptage. Lire l'un sans l'autre fait conclure
       qu'aucune écriture de cache n'a eu lieu — alors qu'on vient de la payer.
    """
    donnees = getattr(message, "usage_metadata", None) or {}
    details = donnees.get("input_token_details") or {}
    ecrit = (
        details.get("cache_creation")
        or details.get("ephemeral_5m_input_tokens")
        or details.get("ephemeral_1h_input_tokens")
        or 0
    )
    return Usage(
        entree=donnees.get("input_tokens") or 0,
        sortie=donnees.get("output_tokens") or 0,
        cache_lu=details.get("cache_read") or 0,
        cache_ecrit=ecrit,
    )


def _finir(
    agent: Agent,
    texte: str,
    requetes: list[RequeteExecutee],
    arret: Arret,
    usage: Usage,
    identifiant: str,
) -> AgentResponse:
    logger.info(
        "ask · %s · %d requête(s) dont %d en échec · %d tokens (cache lu %d, écrit %d)",
        arret.value,
        len(requetes),
        sum(1 for r in requetes if not r.a_reussi),
        usage.entree + usage.sortie,
        usage.cache_lu,
        usage.cache_ecrit,
    )
    return AgentResponse(
        texte=texte,
        requetes=requetes,
        arret=arret,
        usage=usage,
        modele=identifiant,
        empreinte_prompt=agent.empreinte_prompt,
    )
