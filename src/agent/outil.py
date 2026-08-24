"""Le seul outil du modèle, et le pont vers `src/db/sql.py`.

Deux choses distinctes vivent ici, et il vaut la peine de les séparer :

- **la description** donnée au modèle — du texte, qui appartient au préfixe mis en cache ;
- **l'exécution** — du code, qui traduit une sortie de `run_sql` en type du noyau.

Le schéma est écrit à la main plutôt que dérivé d'une fonction Python décorée. Ce n'est
pas de la coquetterie : il s'assemble **avant** le prompt système dans la requête, donc
il fait partie du préfixe. Un docstring reformulé par mégarde invaliderait tout le cache,
sans erreur ni avertissement. Écrit ici, il se relit, se teste, et son empreinte se
surveille.
"""

from __future__ import annotations

import logging

from src.db import sql
from src.agent.reponse import RequeteExecutee

logger = logging.getLogger(__name__)

NOM = "run_sql"

# Description prescriptive : elle dit *quand* appeler l'outil, pas seulement ce qu'il
# fait. C'est ce qui pèse sur le taux de sollicitation — un modèle qui hésite à
# interroger la base répond de mémoire, et le prompt lui interdit précisément ça.
OUTIL_SQL: dict = {
    "name": NOM,
    "description": (
        "Exécute une requête SQL de lecture sur la base DuckDB décrite dans le prompt "
        "système, et renvoie le résultat sous forme de tableau.\n\n"
        "Appelle cet outil dès qu'une réponse dépend d'une valeur des données — un "
        "total, une évolution, une liste, une vérification d'existence. C'est le seul "
        "accès à la base : aucun chiffre ne peut venir d'ailleurs.\n\n"
        "Une seule instruction par appel, et uniquement du SELECT (les CTE `WITH … "
        "SELECT` et `DESCRIBE` sont acceptés). Le nombre de lignes renvoyées est "
        "plafonné, et la troncature est signalée dans le résultat : quand elle "
        "survient, agréger ou filtrer plutôt que de conclure sur un extrait. Les "
        "requêtes trop longues sont interrompues.\n\n"
        "Si la requête est refusée ou échoue, le message d'erreur contient de quoi la "
        "corriger — le lire et réessayer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "La requête SELECT à exécuter, sans point-virgule final.",
            }
        },
        "required": ["query"],
    },
}


def executer(query: str, con=None) -> RequeteExecutee:
    """Exécute une requête et renvoie son issue — sans jamais lever.

    Les trois exceptions de `sql.py` deviennent un champ `erreur`. Ce n'est pas de
    l'étouffement : le message est rendu au modèle, qui doit s'en servir pour se
    reprendre, et il reste dans la réponse pour que le harnais compte les tâtonnements.
    Une exception qui remonterait ici arrêterait une boucle qui a encore trois essais.
    """
    try:
        resultat = sql.run_sql(query, con)
    except (sql.SqlRefuse, sql.SqlInvalide, sql.SqlTropLong) as exc:
        # Jamais le résultat, jamais la question de l'utilisateur : le journal doit
        # rester exploitable sans devenir une donnée à protéger comme la base.
        logger.info("sql refusé ou en échec · %s", " ".join(query.split())[:120])
        return RequeteExecutee(sql=query, erreur=str(exc))

    return RequeteExecutee(
        sql=query,
        colonnes=list(resultat.colonnes),
        lignes=list(resultat.lignes),
        tronque=resultat.tronque,
        duree_ms=resultat.duree_ms,
        tables=list(resultat.tables),
    )


def en_texte(executee: RequeteExecutee) -> str:
    """Ce que le modèle lit en retour d'outil."""
    if executee.erreur is not None:
        return executee.erreur

    return sql.en_texte(
        sql.ResultatSql(
            colonnes=executee.colonnes,
            lignes=executee.lignes,
            tronque=executee.tronque,
            duree_ms=executee.duree_ms,
        )
    )
