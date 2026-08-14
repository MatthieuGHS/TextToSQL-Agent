"""Budget de complexité du dispositif de mesure.

Ce fichier ne teste pas un comportement : il tient une limite. Il existe parce que la
complexité de l'instrument est devenue, mesures à l'appui, la principale source de
défauts du projet — les quatre derniers venaient tous de `tests/eval`, aucun de l'agent,
et chacun d'un mécanisme ajouté au tour précédent pour couvrir le mécanisme d'avant.

Une règle de méthode écrite dans `docs/decisions.md` se contourne, comme une règle de
prompt. Celle-ci est donc verrouillée dans le code, au même titre que les plafonds de la
boucle : *ce qui doit être vrai à chaque fois n'appartient pas à la prose*.

**Le budget porte sur les lignes exécutables**, docstrings et commentaires exclus. Les
conventions du dépôt demandent des commentaires qui portent le raisonnement ; un budget
qui les compterait pousserait à les supprimer, c'est-à-dire à rendre le code moins
reprenable pour satisfaire une métrique. C'est le mécanisme qu'on veut borner, pas son
explication.

Quand ce test échoue, la question n'est pas « de combien relever le plafond ? » mais
**« quelle assertion existante n'a jamais échoué ? »** — le rapport d'évaluation donne la
réponse, et elle est connue pour plusieurs d'entre elles.
"""

from __future__ import annotations

import ast
import pathlib

DOSSIER = pathlib.Path(__file__).resolve().parent / "eval"

# Mesuré le 14/08/2026, après les corrections de la relecture méthodologique. Le plafond
# est la valeur du jour, sans marge : une marge serait dépensée.
BUDGET_EXECUTABLE = 1058


def _lignes_executables(chemin: pathlib.Path) -> int:
    """Lignes non vides, hors commentaires et hors docstrings."""
    source = chemin.read_text(encoding="utf-8")
    porteurs = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    docstrings: set[int] = set()
    for noeud in ast.walk(ast.parse(source)):
        if not isinstance(noeud, porteurs) or not noeud.body:
            continue
        premier = noeud.body[0]
        if (
            isinstance(premier, ast.Expr)
            and isinstance(premier.value, ast.Constant)
            and isinstance(premier.value.value, str)
        ):
            docstrings.update(range(premier.lineno, premier.end_lineno + 1))

    return sum(
        1
        for numero, ligne in enumerate(source.splitlines(), 1)
        if ligne.strip()
        and not ligne.strip().startswith("#")
        and numero not in docstrings
    )


def test_l_instrument_de_mesure_reste_sous_son_budget():
    """Toute assertion ajoutée en compense une autre.

    Repère utile pour lire ce chiffre : le noyau mesuré — ETL, accès base et agent —
    pesait 1 818 lignes au moment où ce budget a été posé. L'instrument n'a aucune raison
    de le rattraper.
    """
    par_fichier = {
        f.name: _lignes_executables(f) for f in sorted(DOSSIER.glob("*.py"))
    }
    total = sum(par_fichier.values())

    assert total <= BUDGET_EXECUTABLE, (
        f"le dispositif de mesure passe à {total} lignes exécutables pour un budget de "
        f"{BUDGET_EXECUTABLE} : {par_fichier}. Avant de relever le plafond, chercher "
        f"l'assertion qui n'a jamais échoué."
    )
