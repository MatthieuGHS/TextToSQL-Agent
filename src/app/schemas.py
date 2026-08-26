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

    `graphique` : la spécification que ce résultat *permettrait*, calculée par les mêmes
    règles que le graphique principal. L'interface l'affiche à la demande — le tracé
    automatique reste réservé à la dernière requête, celle qui porte la conclusion ;
    les précédentes sont des explorations, et les tracer d'office illustrerait le
    raisonnement au lieu de la réponse.
    """

    sql: str
    colonnes: list[str]
    lignes: list[list[Any]]
    tronque: bool
    duree_ms: int
    erreur: str | None
    # Les tables lues, décidées par le parseur du moteur — jamais par une recherche de
    # noms dans le texte, qu'un littéral tromperait. Vide sur une requête en échec.
    tables: list[str]
    # Écrit par le modèle avant d'exécuter, pour qui relit. Demandé par le client pour du
    # débogage : voir l'intention à côté du SQL dit pourquoi une requête est ce qu'elle est.
    raisonnement: str
    # Le bloc que l'interface ouvre d'emblée : celui d'où sort le graphique principal,
    # donc celui qui porte la conclusion. Décidé ici et non côté navigateur — le rejouer
    # là-bas le ferait diverger de `boucle._graphique` à la première règle ajoutée, et
    # l'interface ouvrirait alors un bloc d'exploration en le présentant comme la
    # conclusion. Faux sur toutes les requêtes quand il n'y a pas de graphique.
    porte_la_conclusion: bool = False
    # Défini plus bas dans le module ; résolu à la première validation.
    graphique: GraphiqueSortant | None


class SerieSortante(BaseModel):
    colonne: str
    # Les `None` sont conservés : un trou dans une série est une information, et
    # l'interface doit le rendre comme une interruption de tracé, pas comme un zéro.
    valeurs: list[float | None]
    axe_secondaire: bool


class GraphiqueSortant(BaseModel):
    """La spécification décidée par `src/charts`, jamais par l'interface.

    Celle-ci ne choisit ni le type, ni les axes, ni ce qui se trace : elle rend. C'est ce
    qui permet de changer de bibliothèque de rendu sans toucher à une règle de lisibilité,
    et de tester ces règles sur des fonctions pures.

    Absent de la réponse quand il n'y a rien à tracer — et c'est le cas fréquent.
    """

    type: str
    x: str
    etiquettes: list[Any]
    series: list[SerieSortante]
    # Ce que l'interface a le droit d'offrir en bascule : décidé par `src/charts`,
    # jamais élargi côté navigateur.
    variantes: list[str]
    empilable: bool


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
    graphique: GraphiqueSortant | None
    arret: str
    arret_normal: bool
    usage: UsageSortant
    modele: str


class FichierSource(BaseModel):
    """L'état d'un des fichiers attendus par la pipeline."""

    nom: str
    present: bool
    octets: int | None
    modifie_le: str | None
    # La pipeline ne lit pas `features.csv` pour construire : elle s'en sert pour vérifier
    # que les trois vues la couvrent. Le distinguer évite de le présenter comme un intrant.
    requis: bool


class EtatDonnees(BaseModel):
    fichiers: list[FichierSource]
    base_presente: bool
    base_modifiee_le: str | None
    tables: list[str]


class Sante(BaseModel):
    """Ce qu'on regarde avant une démonstration, pas pendant."""

    base_presente: bool
    cle_chargee: bool
    modele: str
    tables: list[str]
