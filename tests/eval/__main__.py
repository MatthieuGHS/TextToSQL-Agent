"""Lancement d'une campagne d'évaluation.

    python -m tests.eval --a-blanc          # rejoue le cache, aucun appel
    python -m tests.eval --k 1 --source corpus
    python -m tests.eval                    # campagne de référence

**Ce fichier n'est pas un test**, et c'est délibéré : `pytest tests/` ne le collecte pas,
donc la suite reste gratuite. C'est le seul endroit du dépôt d'où partent des appels
facturés, et il faut le vouloir pour l'exécuter.

L'ordre d'une campagne n'est pas un détail d'exécution, c'est la méthode :

1. **peuplement** — `--k 1`, corpus seul. On paie une fois de vraies réponses.
2. **à blanc** — `--a-blanc`, autant de fois qu'il faut. Les assertions n'ont jamais vu de
   sortie réelle : elles seront fausses avant d'être justes, et on les corrige ici sans
   rien repayer.
3. **référence** — la campagne complète, qui devient la ligne de base.

Entre 2 et 3, on ne touche ni au prompt ni à l'agent. Sinon la ligne de base mesure un
système qu'on a bougé en le regardant.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import sys

from src.agent import boucle
from src.db import connexion
from tests.eval import agent_reel, grille, runner
from tests.eval.corpus import corpus_de_controle, corpus_de_travail

RACINE_EVAL = connexion.ROOT / "data" / "eval"

# Le scellé. Trois propriétés tirées d'avance, graine publiée, à n'ouvrir qu'à la fin
# d'E8 : c'est ce qui distingue une mesure d'un ajustement rétrospectif. L'option existe
# parce qu'il faudra bien l'ouvrir un jour, la phrase parce qu'on ne l'ouvre pas par
# distraction.
CONFIRMATION_CONTROLE = "j'ouvre le scellé"


def _cas(source: str, controle: bool) -> tuple[tuple, tuple]:
    """Renvoie (cas du corpus, cas de la grille). Séparés : leurs k diffèrent."""
    du_corpus = corpus_de_controle() if controle else corpus_de_travail()
    de_la_grille = grille.charger(grille.chemin_par_defaut(connexion.ROOT))

    if source == "corpus":
        return du_corpus, ()
    if source == "grille":
        return (), de_la_grille
    return du_corpus, de_la_grille


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(
        prog="python -m tests.eval",
        description="Joue le corpus d'évaluation contre l'agent et produit un rapport.",
    )
    a.add_argument("--k", type=int, default=3,
                   help="répétitions par question du corpus (défaut 3)")
    a.add_argument("--k-grille", type=int, default=1,
                   help="répétitions sur la grille client (défaut 1 : elle se note "
                        "à la main, les répétitions n'y mesurent rien)")
    a.add_argument("--effort", default=None, choices=["low", "medium", "high"],
                   help="profondeur de raisonnement (défaut : celui de la boucle)")
    a.add_argument("--source", default="tout", choices=["corpus", "grille", "tout"])
    a.add_argument("--a-blanc", action="store_true",
                   help="rejoue le cache sans un seul appel API")
    a.add_argument("--controle", metavar="CONFIRMATION", default=None,
                   help=f"exécute le jeu sous scellé ; exige « {CONFIRMATION_CONTROLE} »")
    a.add_argument("--journal", action="store_true")
    args = a.parse_args(argv)

    if args.journal:
        logging.basicConfig(
            level=logging.INFO, format="\033[2m%(name)s · %(message)s\033[0m"
        )

    controle = args.controle is not None
    if controle and args.controle != CONFIRMATION_CONTROLE:
        print(
            f"Le jeu de contrôle est sous scellé jusqu'à la fin d'E8 : l'exécuter avant\n"
            f"en fait un jeu de réglage comme un autre, et il ne mesure plus rien.\n"
            f"Pour l'ouvrir volontairement : --controle \"{CONFIRMATION_CONTROLE}\"",
            file=sys.stderr,
        )
        return 2

    # Garde-fou mécanique, et non une convention de nommage : ce module vit dans `tests/`,
    # et il suffirait d'un `main([])` dans un fichier de test pour que chaque exécution de
    # la suite parte consommer une campagne entière. Le mode à blanc, lui, reste permis —
    # il ne coûte rien, et c'est exactement ce qu'un test a besoin d'exercer.
    if not args.a_blanc and "pytest" in sys.modules:
        raise RuntimeError(
            "campagne réelle lancée depuis la suite de tests : elle consommerait des "
            "appels API à chaque exécution. Utiliser --a-blanc, ou lancer "
            "`python -m tests.eval` en ligne de commande."
        )

    con = connexion.ouvrir()
    cache = runner.Cache(RACINE_EVAL / "cache")

    try:
        if args.a_blanc:
            agent = agent_reel.hors_ligne(RACINE_EVAL, con)
        else:
            agent = agent_reel.construire(
                RACINE_EVAL, effort=args.effort or boucle.EFFORT
            )

        du_corpus, de_la_grille = _cas(args.source, controle)
        ecartees: list[str] = []
        executions = []

        for cas, k in ((du_corpus, args.k), (de_la_grille, args.k_grille)):
            if cas:
                executions += runner.executer(
                    cas, agent, con, k=k, cache=cache,
                    a_blanc=args.a_blanc, ecartees=ecartees,
                )
    finally:
        con.close()

    texte = runner.rapport(
        executions, agent, args.k,
        effort=getattr(agent, "effort", ""), ecartees=ecartees,
    )
    # Dédoublonné par question : la notation se fait une fois par question du client, pas
    # une fois par répétition. Avec `--k-grille 3`, le tableau tripliquerait ses lignes.
    vus: dict[str, object] = {}
    for e in executions:
        vus.setdefault(e.cas.question, e.cas)
    note = grille.grille_de_notation(tuple(vus.values()))
    if note:
        texte += "\n\n" + note

    dossier = RACINE_EVAL / "rapports"
    dossier.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    chemin = dossier / f"{horodatage}.md"
    chemin.write_text(texte, encoding="utf-8")

    print(texte)
    print(f"\n\033[2mRapport écrit dans {chemin}\033[0m")
    return 0 if executions else 1


if __name__ == "__main__":
    sys.exit(main())
