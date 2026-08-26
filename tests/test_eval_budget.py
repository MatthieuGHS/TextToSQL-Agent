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

Quand ce test échoue, deux issues sont légitimes, et la première n'est pas celle qu'on
croit.

**« Quelle assertion ne *peut pas* échouer ? »** — et non « laquelle n'a jamais échoué ».
La distinction a été mesurée le 18/08/2026 sur les 171 exécutions en cache : 9 familles
d'assertions sur 11 n'ont jamais produit un seul verdict rouge, soit 342 verdicts à 0 %.
La question d'origine désigne donc presque tout l'instrument, dont `SqlNeTouchePas`, qui
garde le principal piège du jeu de données et n'a jamais échoué parce que l'agent a eu
raison à chaque fois. La supprimer serait le contraire de ce qu'il faut faire. Une seule
famille est structurellement inerte — `PasDeGraphiqueSurResultatVide`, et E7 la réactive.

**Ou relever le plafond, en écrivant pourquoi à côté de la constante.** Un budget qui
n'offre que la suppression transforme un chiffre en objectif, et pousse à retirer des
gardes qui fonctionnent pour tenir une métrique. La trace écrite est ce qui distingue un
relèvement justifié d'un plafond qui cède.
"""

from __future__ import annotations

import ast
import pathlib

DOSSIER = pathlib.Path(__file__).resolve().parent / "eval"

# Le plafond est la valeur du jour, sans marge : une marge serait dépensée. Chaque
# relèvement porte son motif — c'est ce qui le rend relisible, et ce qui rendrait un
# relèvement non motivé visible comme tel.
#
# 14/08/2026 · 1 058 — mesure initiale, après la relecture méthodologique.
# 18/08/2026 · 1 063 — complétion de la clé de réglages : les trois bornes de `run_sql` et
#   la description d'outil changent la réponse du modèle et n'entraient dans aucune clé.
#   Aucune assertion retirée en compensation : la mesure du même jour a montré que la
#   seule famille structurellement inerte est celle qu'E7 réactive.
# 24/08/2026 · 1 063 inchangé — le modèle est entré dans la clé du **registre** des
#   campagnes, qui ne contenait que l'empreinte de réglages, et ça n'a rien coûté. Le
#   relèvement à 1 064 avait d'abord été pris, puis **rendu** : extraire `enregistrer()`,
#   seul écrivain du registre, a libéré exactement les lignes que la clé complète avait
#   demandées. Trace gardée parce qu'elle vaut mieux que le chiffre — la correction qui
#   retire du mécanisme paie souvent celle qui en ajoute, et un plafond qui monte sans
#   qu'on ait cherché est un plafond qui cède.
# 25/08/2026 · 1 067 — complétion de la clé de réglages, encore, et pour la même famille
#   de défaut : les six textes de repli **sont** `Resultat.reponse` sur tout arrêt anormal,
#   donc la chaîne que lisent les assertions, et le flux de contrôle n'était indexé par
#   rien — le tour de rédaction du plafond change la réponse sans toucher une constante.
#   Aucune assertion retirée en compensation, et cette fois sans avoir cherché longtemps :
#   la seule famille structurellement inerte était celle qu'E7 a réactivée, et les quatre
#   lignes ajoutées ne sont pas une assertion mais de l'indexation — exactement ce que le
#   relèvement du 18/08 avait déjà admis. Le budget borne ce que l'instrument *juge*, pas
#   ce qui l'empêche de juger sur des réponses périmées.
BUDGET_EXECUTABLE = 1067


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
        f"{BUDGET_EXECUTABLE} : {par_fichier}.\n"
        f"Deux issues légitimes : retirer une assertion qui ne *peut pas* échouer (et non "
        f"une qui n'a jamais échoué — 9 familles sur 11 sont dans ce cas), ou relever le "
        f"plafond en écrivant le motif à côté de la constante."
    )
