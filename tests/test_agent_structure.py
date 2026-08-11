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

# Les trois façons d'obtenir un agent qui parle réellement à l'API. Aucun fichier de test
# ne doit les appeler ; les modules du harnais, eux, sont faits pour ça.
MARQUEURS_APPEL_REEL = (
    "boucle.construire(",
    "agent_par_defaut(",
    "agent_reel.construire(",
    "identifiant_exact(",
)

# Ce fichier-ci énumère les marqueurs, il ne les appelle pas. Sans cette exception il se
# détecterait lui-même — même raison que `AUTORISES_MODELE` un peu plus haut.
AUTORISES_APPEL_REEL = {"tests/test_agent_structure.py"}


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


def test_aucun_test_ne_declenche_un_appel_api():
    """La suite complète doit rester gratuite, et ça vient de se jouer.

    Jusqu'à E5, aucun code de `tests/` ne savait joindre l'API : l'invariant tenait tout
    seul. Le harnais y ouvre le chemin, et `tests/eval/agent_reel.py` est fait pour
    l'emprunter — mais depuis un point d'entrée en ligne de commande, jamais depuis un
    fichier collecté par pytest.

    Un `construire()` glissé dans un test ne lèverait aucune erreur : il partirait
    consommer des tokens à chaque exécution de la suite, y compris à chaque sauvegarde.
    C'est exactement le genre de coût qu'on ne remarque que sur la facture.
    """
    fautifs = [
        (chemin, marqueur)
        for chemin, code in sources("tests")
        if pathlib.Path(chemin).name.startswith("test_")
        and chemin not in AUTORISES_APPEL_REEL
        for marqueur in MARQUEURS_APPEL_REEL
        if marqueur in code
    ]

    assert not fautifs, (
        f"Ces fichiers de test construisent un agent qui appelle réellement l'API : "
        f"{fautifs}. Injecter un faux modèle, comme le fait tests/test_eval_agent_reel.py."
    )
