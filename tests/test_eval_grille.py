"""Tests du chargeur de grille.

Le harnais doit rester exécutable sans les données client : un dépôt repris sans
`data/raw/` ne doit pas planter, il doit simplement ne pas avoir de grille à jouer.
"""

from __future__ import annotations

import pytest

from src.etl import build_db
from tests.eval import grille


def test_fichier_absent_ne_leve_pas(tmp_path):
    assert grille.charger(tmp_path / "inexistant.xlsx") == ()


def test_colonne_manquante_leve_un_message_utile(tmp_path):
    import pandas as pd

    chemin = tmp_path / "grille.xlsx"
    pd.DataFrame({"#": [1.0], "Autre": ["x"]}).to_excel(chemin, index=False)

    with pytest.raises(ValueError, match="Question"):
        grille.charger(chemin)


def test_chargement_de_la_vraie_grille():
    chemin = grille.chemin_par_defaut(build_db.ROOT)
    if not chemin.exists():
        pytest.skip("grille absente de data/raw/")

    cas = grille.charger(chemin)

    assert len(cas) >= 15, "grille anormalement courte"
    assert all(c.source == "grille" for c in cas)
    assert all(c.question for c in cas)
    assert all(c.propriete.startswith("grille/") for c in cas)
    # Les propriétés du client ne doivent jamais se mélanger aux nôtres.
    from tests.eval import corpus

    assert not {c.propriete for c in cas} & set(corpus.proprietes())


def test_grille_de_notation_reprend_les_colonnes_du_client():
    chemin = grille.chemin_par_defaut(build_db.ROOT)
    if not chemin.exists():
        pytest.skip("grille absente de data/raw/")

    texte = grille.grille_de_notation(grille.charger(chemin))

    assert "SQL Check" in texte
    assert "Pédagogie IA" in texte


def test_ligne_sans_type_ne_produit_pas_une_propriete_nan(tmp_path):
    """`str()` d'une cellule vide de pandas vaut "nan" : sans filtre, la propriété
    s'appellerait « grille/nan » et polluerait le rapport."""
    import pandas as pd

    chemin = tmp_path / "grille.xlsx"
    pd.DataFrame(
        {"#": [1.0], "Question": ["Combien ?"], "Type de question": [None]}
    ).to_excel(chemin, index=False)

    (cas,) = grille.charger(chemin)

    assert cas.propriete == "grille/non typée"


def test_les_lignes_de_service_du_tableur_sont_ecartees(tmp_path):
    """Le tableur contient son propre mode d'emploi dans la colonne Question.

    Un filtre sur le seul vide le laisserait passer : on poserait le mode d'emploi à
    l'agent et le score s'en trouverait faussé sans que rien ne le signale. Le critère
    retenu est la présence d'un numéro.
    """
    import pandas as pd

    chemin = tmp_path / "grille.xlsx"
    pd.DataFrame(
        {
            "#": [1.0, None],
            "Question": [
                "Combien ?",
                "Consigne de remplissage du tableau, pas une question.",
            ],
            "Type de question": ["Data Discovery", None],
        }
    ).to_excel(chemin, index=False)

    cas = grille.charger(chemin)

    assert len(cas) == 1
    assert cas[0].question == "Combien ?"


def test_colonne_numero_manquante_leve(tmp_path):
    """Sans numéro, le filtre écarterait tout : un harnais vide et muet."""
    import pandas as pd

    chemin = tmp_path / "grille.xlsx"
    pd.DataFrame({"Question": ["Combien ?"]}).to_excel(chemin, index=False)

    with pytest.raises(ValueError, match="#"):
        grille.charger(chemin)
