"""Exécution du corpus et production du rapport.

Trois exigences de mesure, toutes issues du plan :

1. **k répétitions.** La génération est stochastique et le modèle retenu refuse
   `temperature` : le déterminisme est hors d'atteinte. On mesure donc la variance au
   lieu de l'ignorer. Le rapport donne un taux de réussite, jamais un booléen.
2. **Modèle épinglé.** L'identifiant exact et l'empreinte du prompt sont enregistrés :
   deux mesures prises sous le même alias à des dates différentes ne sont pas comparables,
   et toute la boucle de réglage repose sur des comparaisons avant/après.
3. **Mode à blanc.** Chaque exécution est mise en cache. Rejouer les assertions sur ce
   cache ne coûte aucun appel — et les assertions seront fausses plusieurs fois avant
   d'être justes.

L'agent est injecté : ce module ne sait pas comment une réponse est produite, ce qui le
rend exerçable avec un bouchon avant que l'agent existe.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import pathlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Protocol, Sequence

import duckdb

from src.agent.reponse import Arret
from tests.eval.assertions import Resultat, Verdict
from tests.eval.corpus import ASSERTIONS_UNIVERSELLES, Cas

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReponseAgent:
    resultat: Resultat
    usage: dict = field(default_factory=dict)


class ErreurApi(Exception):
    """Le service de modèle n'a pas répondu.

    Définie ici et non chez l'adaptateur parce qu'elle fait partie du contrat : c'est la
    seule chose qu'un agent a le droit de lever, et le runner sait quoi en faire. Toute
    autre exception traverse et arrête la campagne — c'est voulu.
    """


class Agent(Protocol):
    """Ce que le harnais attend d'un agent, et rien de plus."""

    identifiant: str
    """Identifiant exact du modèle — jamais un alias, il évoluerait sans prévenir."""

    empreinte_prompt: str
    """Hachage du prompt système : un prompt modifié invalide le cache."""

    def __call__(self, question: str) -> ReponseAgent: ...


@dataclass(frozen=True)
class Execution:
    cas: Cas
    repetition: int
    reponse: ReponseAgent
    verdicts: tuple[Verdict, ...]
    depuis_le_cache: bool

    @property
    def ok(self) -> bool:
        return all(v.ok for v in self.verdicts)

    @property
    def echecs(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.ok]


class Cache:
    """Trace des exécutions, indexée par prompt, modèle, question et répétition.

    Un changement de prompt ou de modèle change la clé : le cache se périme de lui-même,
    sans invalidation manuelle à oublier.
    """

    def __init__(self, racine: pathlib.Path):
        self.racine = racine
        self.racine.mkdir(parents=True, exist_ok=True)

    def _clef(self, agent: Agent, question: str, repetition: int) -> pathlib.Path:
        brut = f"{agent.identifiant}|{agent.empreinte_prompt}|{question}|{repetition}"
        return self.racine / f"{hashlib.sha256(brut.encode()).hexdigest()[:24]}.json"

    def lire(self, agent: Agent, question: str, repetition: int) -> ReponseAgent | None:
        chemin = self._clef(agent, question, repetition)
        if not chemin.exists():
            return None
        brut = json.loads(chemin.read_text())
        return ReponseAgent(Resultat(**brut["resultat"]), brut.get("usage", {}))

    def ecrire(
        self, agent: Agent, question: str, repetition: int, reponse: ReponseAgent
    ) -> None:
        charge = {
            "modele": agent.identifiant,
            "empreinte_prompt": agent.empreinte_prompt,
            "question": question,
            "repetition": repetition,
            "resultat": asdict(reponse.resultat),
            "usage": reponse.usage,
        }
        self._clef(agent, question, repetition).write_text(
            json.dumps(charge, ensure_ascii=False, indent=2)
        )


def executer(
    cas: tuple[Cas, ...],
    agent: Agent,
    con: duckdb.DuckDBPyConnection,
    *,
    k: int = 3,
    cache: Cache | None = None,
    a_blanc: bool = False,
    ecartees: list[str] | None = None,
) -> list[Execution]:
    """Joue le corpus k fois et vérifie les assertions.

    Args:
        a_blanc: n'appelle jamais l'agent. Une exécution absente du cache est ignorée,
            ce qui permet d'itérer sur les assertions sans repayer le corpus.
        ecartees: reçoit les questions dont l'appel a échoué côté service. Une liste et
            non un compteur : savoir *lesquelles* permet de les rejouer.
    """
    executions: list[Execution] = []

    for c in cas:
        for i in range(k):
            depuis_cache = False
            reponse = cache.lire(agent, c.question, i) if cache else None

            if reponse is not None:
                depuis_cache = True
            elif a_blanc:
                continue
            else:
                try:
                    reponse = agent(c.question)
                except ErreurApi:
                    # Écartée, pas comptée en échec : une panne de service n'est pas un
                    # défaut de l'agent, et l'inscrire au score le ferait varier avec la
                    # météo de l'infrastructure. Rien n'est mis en cache non plus — sinon
                    # la panne se figerait dans la mesure et se rejouerait à blanc.
                    logger.warning("appel écarté · %s", c.question[:80])
                    if ecartees is not None:
                        ecartees.append(c.question)
                    continue
                if cache:
                    cache.ecrire(agent, c.question, i, reponse)

            verdicts = tuple(
                a.verifier(reponse.resultat, con)
                for a in (*c.assertions, *ASSERTIONS_UNIVERSELLES)
            )
            executions.append(Execution(c, i, reponse, verdicts, depuis_cache))

    return executions


# --- Agrégation -----------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    reussites: int
    total: int

    @property
    def taux(self) -> float:
        return self.reussites / self.total if self.total else 0.0

    @property
    def lecture(self) -> str:
        """Un cas qui passe une fois sur deux n'est ni acquis ni perdu : il est instable.

        C'est le diagnostic le plus utile — il signale une description ambiguë, et ne se
        traite pas en relançant jusqu'à obtenir un plein.
        """
        if self.total == 0:
            return "non mesuré"
        if self.reussites == self.total:
            return "acquis"
        if self.reussites == 0:
            return "échec"
        return "instable"

    def __str__(self) -> str:
        return f"{self.reussites}/{self.total}"


def par_propriete(executions: list[Execution]) -> dict[str, Score]:
    compteur: dict[str, list[bool]] = defaultdict(list)
    for e in executions:
        compteur[e.cas.propriete].append(e.ok)
    return {p: Score(sum(v), len(v)) for p, v in sorted(compteur.items())}


def par_source(executions: list[Execution]) -> dict[str, Score]:
    compteur: dict[str, list[bool]] = defaultdict(list)
    for e in executions:
        compteur[e.cas.source].append(e.ok)
    return {s: Score(sum(v), len(v)) for s, v in sorted(compteur.items())}


def par_question(executions: list[Execution]) -> dict[str, Score]:
    compteur: dict[str, list[bool]] = defaultdict(list)
    for e in executions:
        compteur[e.cas.question].append(e.ok)
    return {q: Score(sum(v), len(v)) for q, v in compteur.items()}


def cout(executions: list[Execution]) -> dict[str, float]:
    """Coût cumulé, par question, et taux de lecture de cache.

    Piège mesuré au lot 0 : le champ « tokens d'entrée » du connecteur est un **total**
    (caché compris), celui de l'API brute est le **reste non caché**. Ce module attend le
    format du connecteur ; l'agent est responsable de ne pas mélanger les deux.

    Le coût **par question** est le chiffre qui se compare d'une campagne à l'autre : un
    total dépend du nombre de questions et du nombre de répétitions, donc il ne dit rien
    tout seul. Il se calcule sur les exécutions réellement appelées — celles relues du
    cache n'ont rien coûté et dilueraient la moyenne vers le bas.
    """
    entree = sum(e.reponse.usage.get("input_tokens", 0) for e in executions)
    sortie = sum(e.reponse.usage.get("output_tokens", 0) for e in executions)
    relus = sum(e.reponse.usage.get("cache_read", 0) for e in executions)
    appelees = [e for e in executions if not e.depuis_le_cache] or executions
    n = len(appelees)
    return {
        "tokens_entree": entree,
        "tokens_sortie": sortie,
        "tokens_relus_du_cache": relus,
        "taux_de_cache": relus / entree if entree else 0.0,
        "entree_par_question": (
            sum(e.reponse.usage.get("input_tokens", 0) for e in appelees) / n
        ),
        "sortie_par_question": (
            sum(e.reponse.usage.get("output_tokens", 0) for e in appelees) / n
        ),
    }


def par_arret(executions: list[Execution]) -> dict[str, int]:
    """Répartition des motifs d'arrêt.

    Un taux d'arrêts anormaux est un indicateur de qualité au même titre que l'exactitude,
    et il se lit autrement : un plafond d'itérations atteint souvent signale une boucle
    trop courte, une réponse tronquée un plafond de sortie trop bas. Le score seul ne
    distingue pas ces causes.
    """
    compteur: dict[str, int] = defaultdict(int)
    for e in executions:
        compteur[e.reponse.resultat.arret] += 1
    return dict(sorted(compteur.items()))


def tatonnements(executions: list[Execution]) -> tuple[int, int]:
    """Requêtes en échec, et nombre d'exécutions qui en comptent au moins une.

    Elles ne sont pas des fautes — la boucle est faite pour se reprendre — mais leur
    fréquence mesure la clarté du schéma décrit au modèle. Elle baisse quand la
    description s'améliore, ce qui en fait un signal utilisable en E8.
    """
    total = sum(e.reponse.usage.get("tatonnements", 0) for e in executions)
    concernees = sum(1 for e in executions if e.reponse.usage.get("tatonnements", 0))
    return total, concernees


def _sha_du_depot() -> str:
    """Version exacte du code qui a produit la mesure.

    Le modèle et le prompt sont déjà épinglés, mais pas la boucle ni les assertions — or
    elles bougent d'un lot à l'autre. Sans ce repère, deux rapports peuvent différer sans
    qu'on sache si c'est le modèle, le prompt, ou nous.
    """
    import subprocess

    try:
        sortie = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "inconnu"
    return sortie.stdout.strip() or "inconnu"


def rapport(
    executions: list[Execution],
    agent: Agent,
    k: int,
    *,
    effort: str = "",
    ecartees: Sequence[str] = (),
) -> str:
    """Rapport lisible. Le modèle et l'empreinte du prompt y figurent toujours."""
    if not executions:
        return "Aucune exécution — cache vide en mode à blanc ?"

    lignes = [
        "# Rapport d'évaluation",
        "",
        f"- date : {datetime.date.today()}",
        f"- modèle : `{agent.identifiant}`",
        f"- empreinte du prompt : `{agent.empreinte_prompt}`",
        f"- version du code : `{_sha_du_depot()}`",
        *([f"- effort de raisonnement : `{effort}`"] if effort else []),
        f"- répétitions par question : {k}",
        f"- exécutions : {len(executions)}"
        f" (dont {sum(e.depuis_le_cache for e in executions)} depuis le cache)",
    ]

    if ecartees:
        # Écarté n'est pas échoué, mais une campagne trop trouée n'est pas une mesure :
        # le dire en tête plutôt que de laisser lire un score amputé comme s'il était plein.
        lignes += [
            f"- **{len(ecartees)} exécution(s) écartée(s)** pour panne du service, "
            f"non comptées dans les scores — à rejouer avant de conclure.",
        ]

    lignes += [
        "",
        "## Par propriété",
        "",
        "| Propriété | Score | Lecture |",
        "|---|---|---|",
    ]
    for propriete, score in par_propriete(executions).items():
        lignes.append(f"| {propriete} | {score} | {score.lecture} |")

    lignes += ["", "## Par origine", "", "| Origine | Score |", "|---|---|"]
    for source, score in par_source(executions).items():
        lignes.append(f"| {source} | {score} |")

    instables = {
        q: s for q, s in par_question(executions).items() if s.lecture == "instable"
    }
    if instables:
        lignes += ["", "## Questions instables", "",
                   "Le diagnostic le plus utile : une réponse qui varie d'une exécution "
                   "à l'autre signale une description ambiguë.", "",
                   "| Question | Score |", "|---|---|"]
        for question, score in sorted(instables.items()):
            lignes.append(f"| {question} | {score} |")

    echecs: dict[str, set[str]] = defaultdict(set)
    for e in executions:
        for v in e.echecs:
            echecs[e.cas.question].add(v.nom)
    if echecs:
        lignes += ["", "## Assertions en échec", "", "| Question | Assertions |",
                   "|---|---|"]
        for question, noms in sorted(echecs.items()):
            lignes.append(f"| {question} | {', '.join(sorted(noms))} |")

    arrets = par_arret(executions)
    if len(arrets) > 1 or Arret.REPONSE_DONNEE.value not in arrets:
        lignes += ["", "## Motifs d'arrêt", "", "| Motif | Exécutions |", "|---|---|"]
        for motif, n in arrets.items():
            lignes.append(f"| {motif} | {n} |")

    total_tatonnements, avec_tatonnement = tatonnements(executions)
    c = cout(executions)
    lignes += [
        "", "## Coût", "",
        f"- entrée par question : {c['entree_par_question']:,.0f} tokens "
        f"(total {c['tokens_entree']:,})".replace(",", " "),
        f"- sortie par question : {c['sortie_par_question']:,.0f} tokens "
        f"(total {c['tokens_sortie']:,})".replace(",", " "),
        f"- taux de lecture de cache : {c['taux_de_cache']:.0%}",
        f"- requêtes en échec : {total_tatonnements} "
        f"sur {avec_tatonnement} exécution(s) concernée(s)",
        "",
        "> Les tâtonnements ne sont pas des fautes — la boucle est faite pour se "
        "reprendre. Leur fréquence mesure la clarté du schéma décrit au modèle.",
        "",
        "> Quelques dizaines de questions, k répétitions : un écart de quelques points "
        "entre deux itérations n'est pas significatif. Seules les tendances franches "
        "le sont.",
    ]
    return "\n".join(lignes)
