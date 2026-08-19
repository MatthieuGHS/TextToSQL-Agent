"""Interface en ligne de commande — une coquille, rien d'autre.

Elle lit des arguments, appelle `ask()`, met en forme. Aucune décision métier ne se prend
ici : ce fichier doit pouvoir être supprimé sans que le moteur bouge d'une ligne. C'est le
premier endroit où cette discipline se teste vraiment, parce que c'est le premier moment
où « juste une petite règle ici » devient tentant.

Elle a tenu : `src/app/` est venue s'ajouter à côté sans rien déplacer du noyau, et les
deux interfaces coexistent aujourd'hui sur le même `ask()`.

    python -m src.agent "Quel budget média sur la dernière année ?"
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.agent.boucle import ask
from src.agent.reponse import AgentResponse
from src.db import sql


def afficher(reponse: AgentResponse, verbeux: bool) -> None:
    for requete in reponse.requetes:
        etat = "échec" if not requete.a_reussi else f"{len(requete.lignes)} ligne(s)"
        print(f"\n\033[2m── SQL ({etat}) ─────────────────\033[0m")
        print("\033[2m" + " ".join(requete.sql.split()) + "\033[0m")
        if verbeux and requete.a_reussi:
            print(
                sql.en_texte(
                    sql.ResultatSql(
                        requete.colonnes, requete.lignes, requete.tronque, requete.duree_ms
                    )
                )
            )
        elif not requete.a_reussi:
            print("\033[2m" + requete.erreur.splitlines()[0] + "\033[0m")

    print(f"\n{reponse.texte}\n")

    if not reponse.arret.est_normal:
        print(f"\033[33m(arrêt : {reponse.arret.value})\033[0m")

    usage = reponse.usage
    print(
        f"\033[2m{reponse.modele} · prompt {reponse.empreinte_prompt} · "
        f"{usage.entree} tokens entrée dont {usage.cache_lu} lus en cache "
        f"(écrits {usage.cache_ecrit}) · {usage.sortie} sortie\033[0m"
    )


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="python -m src.agent",
        description="Pose une question en français sur les données média.",
    )
    analyseur.add_argument("question", help="la question, entre guillemets")
    analyseur.add_argument(
        "-v", "--verbeux", action="store_true", help="affiche les résultats de requête"
    )
    analyseur.add_argument(
        "--journal", action="store_true", help="affiche le journal d'exécution"
    )
    arguments = analyseur.parse_args(argv)

    if arguments.journal:
        logging.basicConfig(
            level=logging.INFO, format="\033[2m%(name)s · %(message)s\033[0m"
        )

    reponse = ask(arguments.question)
    afficher(reponse, arguments.verbeux)
    return 0 if reponse.arret.est_normal else 1


if __name__ == "__main__":
    sys.exit(main())
