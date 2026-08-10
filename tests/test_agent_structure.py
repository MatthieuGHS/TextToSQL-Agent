"""Les invariants d'architecture du noyau, vérifiés mécaniquement.

Même esprit que `test_db_point_unique.py` : une propriété sur laquelle tout le reste
repose ne doit pas dépendre de la discipline de qui écrira la suite. Ces tests lisent du
texte, pas un arbre syntaxique — volontairement grossiers, mais ils attrapent le cas qui
compte : quelqu'un qui, de bonne foi, prend un raccourci.
"""

from __future__ import annotations

import pathlib

from src.db import connexion

RACINE = pathlib.Path(connexion.__file__).resolve().parents[2]

# Le seul module dont c'est le métier de construire un client de modèle.
AUTORISES_MODELE = {"src/agent/boucle.py"}

# L'interface est une coquille : elle présente, elle ne décide pas. Un module d'interface
# qui appellerait la base ou le modèle rendrait le noyau très coûteux à en extraire.
MARQUEURS_PRESENTATION = ("import streamlit", "import plotly", "st.write", "st.markdown")


def sources(dossier: str) -> list[tuple[str, str]]:
    return [
        (f.relative_to(RACINE).as_posix(), f.read_text(encoding="utf-8"))
        for f in sorted((RACINE / dossier).rglob("*.py"))
    ]


def test_seule_la_boucle_construit_un_client_de_modele():
    """Le prompt, sa configuration et les plafonds sont posés en un seul endroit.

    Un second point de construction, c'est un second jeu de réglages — donc un appel qui
    consomme sans plafond, ou qui laisse le raisonnement au défaut sans que ça se voie.
    """
    fautifs = [
        chemin
        for chemin, code in sources("src")
        if chemin not in AUTORISES_MODELE and "ChatAnthropic(" in code
    ]

    assert not fautifs, (
        f"Ces modules construisent leur propre client de modèle : {fautifs}. "
        f"Les plafonds et la configuration explicite de src/agent/boucle.py sont alors "
        f"contournés."
    )


def test_le_noyau_ne_contient_aucune_logique_de_presentation():
    """`ask()` ne choisit pas ce qui est montré : c'est le métier de la coquille.

    Passer d'une interface locale à une API doit revenir à réécrire la coquille, jamais
    le moteur — et ça se décide dès la première ligne, pas au moment du portage.
    """
    fautifs = [
        (chemin, marqueur)
        for chemin, code in sources("src/agent")
        for marqueur in MARQUEURS_PRESENTATION
        if marqueur in code
    ]

    assert not fautifs, f"logique de présentation dans le noyau : {fautifs}"
