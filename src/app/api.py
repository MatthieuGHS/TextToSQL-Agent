"""L'API HTTP. Une coquille mince — aucune logique métier.

La règle que ce fichier s'impose, et qui est la seule qui compte ici : **si une ligne
décide quelque chose sur les données plutôt que sur le transport, elle est au mauvais
endroit.** Tout ce qui juge, borne ou interprète vit dans `src/agent` et `src/db`. Ce
module traduit du HTTP en appel de fonction, et une réponse en JSON.

Deux points d'entrée pour une même question, et c'est délibéré :

- `POST /api/question` rend la réponse complète. C'est le contrat simple, celui qu'un
  client tiers utiliserait.
- `POST /api/question/flux` rend les étapes au fil de l'eau, puis la réponse. Une question
  demande dix à trente secondes ; une interface qui n'a rien à montrer pendant ce temps
  passe pour figée.

Le second n'est pas une variante du premier : il appelle la même fonction du noyau, avec
un traceur en plus. Aucune logique n'est dupliquée, et `ask()` ne sait pas qu'un des deux
appelants diffuse.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import queue
import threading
from dataclasses import replace

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.agent import boucle
from src.agent.reponse import Echange
from src.app import schemas, serialisation
from src.db import connexion

logger = logging.getLogger(__name__)

RACINE_WEB = connexion.ROOT / "web" / "dist"

# Message rendu au client quand une exception inattendue traverse. Volontairement muet sur
# la cause : le détail part au journal, côté serveur. Un message d'erreur qui recopie une
# trace d'exécution finit par exposer un chemin de fichier ou un extrait de requête.
TEXTE_ERREUR_INTERNE = (
    "Une erreur interne est survenue. La question n'a pas pu être traitée."
)

application = FastAPI(
    title="Agent data — API",
    description="Accès HTTP au noyau conversationnel. Aucune logique métier ici.",
)


def _agent_pour_la_requete() -> tuple[boucle.Agent, object]:
    """Un agent par requête, mais **une seule connexion par requête**.

    `interrupt()` de DuckDB porte sur la connexion et non sur la requête : deux questions
    en vol sur une connexion partagée, et le dépassement de délai de l'une interromprait
    l'autre. C'est la décision que `CLAUDE.md` laissait ouverte depuis E4, tranchée ici.

    Le prompt et son empreinte restent ceux de l'agent partagé — donc construits une fois.
    Les regénérer par requête coûterait une lecture complète du schéma et, surtout,
    rouvrirait le risque d'instabilité du préfixe mis en cache, qui est la moitié de
    l'économie du projet. Seule la connexion est propre à l'appel.
    """
    partage = boucle.agent_par_defaut()
    con = connexion.ouvrir()
    return replace(partage, con=con), con


def _historique(entrants: list[schemas.EchangeEntrant]) -> list[Echange]:
    return [Echange(question=e.question, reponse=e.reponse) for e in entrants]


@application.get("/api/sante", response_model=schemas.Sante)
def sante() -> schemas.Sante:
    """Ce qu'on vérifie avant une démonstration.

    Les trois causes de panne au démarrage sont ici : base absente, clé non chargée,
    schéma inattendu. Les découvrir sur la première question coûte bien plus cher.
    """
    tables: list[str] = []
    base = False
    try:
        con = connexion.ouvrir()
        try:
            tables = sorted(
                r[0] for r in con.execute(
                    "SELECT table_name FROM duckdb_tables()"
                ).fetchall()
            )
            base = True
        finally:
            con.close()
    except FileNotFoundError:
        pass

    return schemas.Sante(
        base_presente=base,
        cle_chargee=bool(os.getenv("ANTHROPIC_API_KEY")),
        modele=boucle.MODELE,
        tables=tables,
    )


@application.post("/api/question", response_model=schemas.ReponseSortante)
def question(entree: schemas.QuestionEntrante) -> schemas.ReponseSortante:
    """La réponse complète, en un aller-retour."""
    agent, con = _agent_pour_la_requete()
    try:
        reponse = boucle.ask(entree.question, _historique(entree.historique), agent=agent)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("question en échec")
        raise HTTPException(status_code=500, detail=TEXTE_ERREUR_INTERNE) from exc
    finally:
        con.close()

    return serialisation.reponse(reponse)


@application.post("/api/question/flux")
def question_en_flux(entree: schemas.QuestionEntrante) -> StreamingResponse:
    """Les étapes au fil de l'eau, puis la réponse. Une ligne de JSON par événement.

    NDJSON plutôt que le format `text/event-stream` : l'interface lit ce flux avec
    `fetch`, pas avec `EventSource` — qui ne sait faire que du GET, donc qui obligerait à
    faire voyager la conversation dans l'URL. Sans `EventSource`, le cadrage SSE
    n'apporterait rien qu'un séparateur de lignes ne donne déjà.

    `ask()` est bloquante : elle tourne dans un fil, et pousse ses étapes dans une file
    que le générateur vide. C'est ce qui permet de diffuser sans rendre le noyau
    asynchrone — une réécriture qui n'aurait servi qu'à ça.
    """
    file: queue.Queue = queue.Queue()

    def tracer(etape: str, detail: dict) -> None:
        file.put({"type": etape, **detail})

    def travail() -> None:
        agent, con = _agent_pour_la_requete()
        try:
            reponse = boucle.ask(
                entree.question, _historique(entree.historique),
                agent=agent, trace=tracer,
            )
            file.put({
                "type": "reponse",
                "reponse": serialisation.reponse(reponse).model_dump(),
            })
        except Exception:
            # Le flux a déjà commencé : le code de statut est parti, on ne peut plus
            # rendre une 500. L'erreur devient donc un événement, que l'interface doit
            # savoir afficher — c'est le prix du streaming, et il est explicite.
            logger.exception("question en flux en échec")
            file.put({"type": "erreur", "message": TEXTE_ERREUR_INTERNE})
        finally:
            con.close()
            file.put(None)

    threading.Thread(target=travail, daemon=True).start()

    def evenements():
        while (evenement := file.get()) is not None:
            yield json.dumps(evenement, ensure_ascii=False) + "\n"

    return StreamingResponse(evenements(), media_type="application/x-ndjson")


# Monté en dernier : la racine attrape tout ce qui n'est pas `/api/…`, et l'ordre de
# déclaration décide. Monté seulement si le build existe — en développement, c'est Vite
# qui sert l'interface et relaie `/api` vers ici.
if RACINE_WEB.is_dir():
    application.mount(
        "/", StaticFiles(directory=RACINE_WEB, html=True), name="interface"
    )
