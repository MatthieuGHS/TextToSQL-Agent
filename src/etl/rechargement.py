"""Rechargement des sources : dossier d'attente, construction, promotion.

Cette orchestration vivait dans `src/app/api.py`, qui s'interdit toute logique métier.
Or la garantie centrale de la page de chargement — *un fichier mal formé ne dégrade ni
les sources ni la base* — est une règle de la pipeline, pas du transport : elle se teste
ici, sans HTTP, et l'API n'en garde que la traduction en flux d'événements.

Le geste en trois temps :

1. **Dossier d'attente.** Les sources en place y sont recopiées, les fichiers reçus
   écrits par-dessus. Rien ne touche encore `data/raw/`.
2. **Construction depuis l'attente.** `build_db.construire` écrit la base de façon
   atomique et valide le contrat de données ; un échec lève, et l'attente est jetée.
3. **Promotion.** Seulement après une base écrite *et* validée, les fichiers reçus
   deviennent les sources de référence. Dans cet ordre, jamais l'inverse.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile

from src.etl import build_db

# Seuls ces noms sont acceptés. La pipeline les code en dur : accepter un autre nom
# serait silencieusement inutile — le fichier serait écrit puis ignoré, et l'utilisateur
# croirait avoir chargé ses données. Effet second, plus important : aucun nom de fichier
# ne vient de l'utilisateur, donc aucune traversée de chemin n'est possible.
FICHIERS_ATTENDUS: dict[str, bool] = {
    **{nom: True for nom in build_db.SOURCES.values()},
    build_db.MASTER_SOURCE: False,
}


def reconstruire_depuis(
    recus: dict[str, bytes],
    base: pathlib.Path,
    raw_dir: pathlib.Path | None = None,
) -> None:
    """Reconstruit la base depuis les sources en place, complétées des fichiers reçus.

    Sans fichier reçu, c'est une simple reconstruction : utile quand le contrat de
    données se met à échouer sans qu'aucune source ait bougé, si le code de vérification
    a changé.

    Les clés de `recus` doivent appartenir à `FICHIERS_ATTENDUS` — c'est à l'appelant de
    refuser le reste *avant* d'entamer le travail, pour que le refus arrive en erreur
    HTTP et non au milieu d'un flux déjà ouvert.

    Raises:
        Les exceptions de la pipeline traversent telles quelles (`DataQualityError`,
        `FileNotFoundError`, `ContextColumnError`…) : l'appelant sait les traduire en
        message utilisateur, et un échec laisse sources et base intactes.
    """
    inattendus = sorted(set(recus) - set(FICHIERS_ATTENDUS))
    if inattendus:
        raise ValueError(f"fichiers hors pipeline : {inattendus}")

    raw = raw_dir if raw_dir is not None else build_db.RAW_DIR
    attente = pathlib.Path(tempfile.mkdtemp(prefix="sources-"))
    try:
        for nom in FICHIERS_ATTENDUS:
            courant = raw / nom
            if courant.is_file():
                (attente / nom).write_bytes(courant.read_bytes())
        for nom, contenu in recus.items():
            (attente / nom).write_bytes(contenu)

        build_db.construire(base, attente)

        # Promotion : la base est écrite et validée, les sources qui l'ont produite
        # deviennent les sources de référence.
        raw.mkdir(parents=True, exist_ok=True)
        for nom in recus:
            (raw / nom).write_bytes((attente / nom).read_bytes())
    finally:
        shutil.rmtree(attente, ignore_errors=True)
