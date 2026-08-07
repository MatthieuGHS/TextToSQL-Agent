"""Le point d'accès à la base est unique — vérifié mécaniquement.

Toutes les garanties de `src/db/sql.py` reposent sur une prémisse : c'est le seul chemin
vers la base. Un module qui ouvrirait sa propre connexion les contournerait toutes, sans
qu'aucun test existant ne s'en aperçoive.

Ce test parcourt les sources et échoue si cette prémisse cesse d'être vraie. Il est
volontairement grossier — il lit du texte, pas un arbre syntaxique — mais il attrape le
cas qui compte : quelqu'un qui, de bonne foi, ouvre une connexion « juste pour un
diagnostic rapide ».
"""

from __future__ import annotations

import pathlib

from src.db import connexion

RACINE = pathlib.Path(connexion.__file__).resolve().parents[2]

# `connexion.py` est l'endroit dont c'est le métier. `etl/build_db.py` construit la base
# avant qu'elle existe : il ne peut pas passer par une connexion en lecture seule.
AUTORISES = {"src/db/connexion.py", "src/etl/build_db.py"}


def test_seul_le_module_de_connexion_ouvre_la_base():
    fautifs = []
    for fichier in sorted((RACINE / "src").rglob("*.py")):
        relatif = fichier.relative_to(RACINE).as_posix()
        if relatif in AUTORISES:
            continue
        if "duckdb.connect" in fichier.read_text():
            fautifs.append(relatif)

    assert not fautifs, (
        f"Ces modules ouvrent la base sans passer par src/db/connexion.ouvrir() : "
        f"{fautifs}. Toutes les protections de src/db/sql.py sont alors contournées."
    )
