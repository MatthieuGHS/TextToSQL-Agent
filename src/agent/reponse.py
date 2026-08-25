"""Ce que le noyau renvoie, et ce que l'interface reçoit.

Ces types **sont** le contrat entre le moteur et tout ce qui le consommera : l'interface
(E8) veut du texte, le harnais d'évaluation (E5) veut des valeurs numériques à asserter,
les graphiques (E7) voudront des lignes. Une chaîne de caractères ne peut pas servir les
trois : il faudrait la réanalyser pour retrouver des nombres qu'on avait déjà, et ça
casserait au premier changement de format.

Aucun de ces types ne dépend de la bibliothèque de modèle, ni de DuckDB. C'est voulu :
remplacer l'une ou l'autre ne doit pas se propager jusqu'ici.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Arret(enum.Enum):
    """Pourquoi la boucle s'est arrêtée.

    Un motif d'arrêt est une **donnée de la réponse**, pas une exception. Une boucle
    interrompue au plafond a quand même produit du travail — des requêtes, parfois une
    réponse partielle — et lever une exception jetterait tout.

    Le harnais d'évaluation compte ces motifs : un taux d'arrêts anormaux est un
    indicateur de qualité, au même titre que l'exactitude des réponses.
    """

    REPONSE_DONNEE = "reponse_donnee"
    REPONSE_TRONQUEE = "reponse_tronquee"
    PLAFOND_ITERATIONS = "plafond_iterations"
    TROP_D_ECHECS_SQL = "trop_d_echecs_sql"
    REFUS_MODELE = "refus_modele"
    ERREUR_API = "erreur_api"

    @property
    def est_normal(self) -> bool:
        """`REPONSE_DONNEE` couvre aussi la question de clarification.

        Le prompt demande au modèle de demander une précision quand la question est
        ambiguë : une réponse sans aucune requête est donc un succès, pas un échec.
        C'est ici que la distinction est posée une fois pour toutes, plutôt que d'être
        rejouée par chaque consommateur.

        `REPONSE_TRONQUEE` en est délibérément exclu, alors que le texte obtenu peut
        paraître complet : une phrase coupée au plafond de sortie reste une réponse dont
        la fin manque, et la compter comme un succès fausserait la mesure dans le sens
        le plus flatteur — celui qu'on ne va pas vérifier.
        """
        return self is Arret.REPONSE_DONNEE


@dataclass(frozen=True)
class Usage:
    """Consommation de tokens, cumulée sur tous les appels d'une question.

    Le détail du cache n'est pas cosmétique : c'est la vérification que le préfixe est
    bien réutilisé. `cache_lu` à zéro au deuxième appel signale un élément variable dans
    le prompt — un défaut qui ne lève aucune erreur et se paie dix fois le prix.
    """

    entree: int = 0
    sortie: int = 0
    cache_lu: int = 0
    cache_ecrit: int = 0

    def __add__(self, autre: Usage) -> Usage:
        return Usage(
            entree=self.entree + autre.entree,
            sortie=self.sortie + autre.sortie,
            cache_lu=self.cache_lu + autre.cache_lu,
            cache_ecrit=self.cache_ecrit + autre.cache_ecrit,
        )


@dataclass(frozen=True)
class RequeteExecutee:
    """Une tentative du modèle — réussie ou non.

    Les échecs figurent ici au même titre que les succès. Sans eux, on ne distinguerait
    pas « le modèle a écrit la bonne requête du premier coup » de « il a tâtonné trois
    fois avant de tomber juste » : deux réponses identiques, deux qualités différentes.
    """

    sql: str
    colonnes: list[str] = field(default_factory=list)
    lignes: list[tuple] = field(default_factory=list)
    tronque: bool = False
    duree_ms: int = 0
    erreur: str | None = None
    # Les tables réellement lues, décidées par le parseur du moteur (`db.sql`). Vide sur
    # une requête en échec : une erreur de syntaxe n'a lu aucune table, et en annoncer
    # une serait une information inventée.
    tables: list[str] = field(default_factory=list)
    # Ce que le modèle cherchait avec cette requête, écrit par lui **avant** de l'exécuter.
    # Purement destiné à qui relit l'exécution : la boucle n'en lit rien, et aucune
    # décision n'en dépend — sans quoi le modèle piloterait le code par de la prose.
    raisonnement: str = ""

    @property
    def a_reussi(self) -> bool:
        return self.erreur is None


@dataclass(frozen=True)
class Echange:
    """Un tour de conversation passé.

    Seul le texte est conservé, pas les requêtes : réinjecter les appels d'outil
    obligerait à réinjecter aussi leurs résultats, exactement appariés, sous peine de
    rejet par l'API — pour un bénéfice nul, le modèle pouvant relancer la requête s'il
    en a besoin.
    """

    question: str
    reponse: str


@dataclass(frozen=True)
class AgentResponse:
    """La réponse complète à une question.

    `modele` et `empreinte_prompt` voyagent avec elle parce que sans eux, deux mesures ne
    sont pas comparables : les alias de modèle évoluent sous le même nom, et un prompt
    modifié périme les réponses mises en cache par le harnais.
    """

    texte: str
    requetes: list[RequeteExecutee]
    arret: Arret
    usage: Usage
    modele: str
    empreinte_prompt: str
    # `Graphique | None` — annoté `Any` pour que ce module reste sans dépendance : il est
    # le contrat entre le noyau et ses consommateurs, et rien de ce qu'il porte ne doit
    # obliger un lecteur à importer autre chose pour le comprendre.
    graphique: Any = None

    @property
    def a_interroge_la_base(self) -> bool:
        return any(r.a_reussi for r in self.requetes)
