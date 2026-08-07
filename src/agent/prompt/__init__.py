"""Assemblage du prompt système.

Le prompt est fait de deux matières :

- **généré** depuis la base (`schema.py`) — tout ce qu'une requête SQL peut établir : les
  colonnes, les valeurs possibles, les bornes temporelles. Généré parce qu'écrit à la
  main, ce serait faux dès le premier rafraîchissement de l'extrait ;
- **écrit** à la main (`*.md`) — ce que les données *signifient*, et ce qu'elles ne
  permettent pas. Aucune requête ne le dira.

Le texte écrit vit dans des fichiers Markdown plutôt que dans des chaînes Python : il se
relit et se corrige sans toucher au code, et un `git diff` reste lisible.

**Ordre des sections.** Il est pédagogique, pas économique : le modèle lit le périmètre des
tables avant leurs valeurs, donc avant de pouvoir écrire une agrégation fausse.

Ce n'est délibérément *pas* un ordre du plus stable au plus volatile — ce serait l'inverse,
la partie générée étant la seule qui bouge à un rafraîchissement des données, alors que les
`.md` ne changent que sous une main humaine. Poser un jour un second point de coupe de cache
demanderait donc de réordonner d'abord, et de mesurer : tout le prompt tient aujourd'hui
dans un seul bloc, et le découper pour économiser sur une partie qui ne change presque
jamais coûterait plus en complexité qu'en tokens.

**Déterminisme.** Ce texte est le préfixe mis en cache. Il ne doit contenir ni horodatage,
ni identifiant variable — voir `tests/test_prompt.py`, qui vérifie que deux constructions
successives produisent les mêmes octets.
"""

from __future__ import annotations

import hashlib
import pathlib

import duckdb

from src.agent.prompt import schema
from src.db import connexion

DOSSIER = pathlib.Path(__file__).parent

# Ordre d'assemblage. `None` marque l'insertion de la partie générée.
SECTIONS: tuple[str | None, ...] = (
    "role.md",
    None,          # schéma, périmètres, valeurs, métriques — générés
    "metier.md",
    "principes.md",
    "exemples.md",
)


def _lire(nom: str) -> str:
    return (DOSSIER / nom).read_text(encoding="utf-8").strip()


def construire(con: duckdb.DuckDBPyConnection | None = None) -> str:
    """Assemble le prompt système complet.

    À appeler **une fois au démarrage**, pas à chaque question : regénérer à chaque appel
    serait inutile et rouvrirait le risque d'instabilité du préfixe.
    """
    propre = con is None
    con = con or connexion.ouvrir()
    try:
        partie_generee = schema.generer(con)
    finally:
        if propre:
            con.close()

    morceaux = [partie_generee if nom is None else _lire(nom) for nom in SECTIONS]
    return "\n\n".join(morceaux) + "\n"


def empreinte(texte: str) -> str:
    """Identifiant court et stable d'une version de prompt.

    Le harnais d'évaluation l'enregistre dans chaque rapport et l'utilise comme clé de
    cache : deux mesures faites sous des prompts différents ne sont pas comparables, et un
    prompt modifié doit périmer les réponses mises en cache.
    """
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12]
