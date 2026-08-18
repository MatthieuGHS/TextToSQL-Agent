"""Les types de la frontière HTTP.

Séparés de `src.agent.reponse` et non réutilisés tels quels, alors qu'ils s'y
ressemblent. La raison n'est pas la pureté : ces deux jeux de types **n'ont pas les mêmes
raisons de changer**. Les types du noyau suivent ce dont l'agent a besoin ; ceux-ci
suivent ce qu'une interface consomme, et une modification de l'un ne doit pas casser
l'autre en silence. Le prix est un module de conversion, et il est visible — c'est
exactement ce qu'on veut d'un couplage.

Ce que la frontière expose en plus du texte, et qui est le cœur de la demande : **les
lignes et colonnes de chaque requête réussie**. Sans elles, l'interface ne pourrait ni
montrer d'où vient un chiffre, ni dessiner quoi que ce soit à E7.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EchangeEntrant(BaseModel):
    """Un tour de conversation passé, tel que l'interface le renvoie.

    C'est l'interface qui détient la conversation, pas le serveur. Le contrat de `ask()`
    l'y invite — « l'état est passé, jamais détenu » — et l'API n'a donc ni session à
    faire expirer, ni mémoire que deux onglets se disputeraient.
    """

    question: str
    reponse: str


class QuestionEntrante(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    historique: list[EchangeEntrant] = Field(default_factory=list)


class RequeteSortante(BaseModel):
    """Une requête produite par l'agent — réussie ou non.

    Les échecs sont exposés au même titre que les succès. Ils ne sont pas des fautes :
    la boucle est faite pour se reprendre, et les masquer donnerait de l'exécution une
    image plus lisse que la réalité.
    """

    sql: str
    colonnes: list[str]
    lignes: list[list[Any]]
    tronque: bool
    duree_ms: int
    erreur: str | None


class UsageSortant(BaseModel):
    entree: int
    sortie: int
    cache_lu: int
    cache_ecrit: int


class ReponseSortante(BaseModel):
    """Ce que l'interface reçoit.

    `arret` en fait partie et ce n'est pas un détail technique : une réponse tronquée ou
    rendue après abandon se lit comme une réponse complète. L'interface doit pouvoir le
    signaler, sinon elle présente un échec comme un succès.
    """

    texte: str
    requetes: list[RequeteSortante]
    arret: str
    arret_normal: bool
    usage: UsageSortant
    modele: str


class Sante(BaseModel):
    """Ce qu'on regarde avant une démonstration, pas pendant."""

    base_presente: bool
    cle_chargee: bool
    modele: str
    tables: list[str]
