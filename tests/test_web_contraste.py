"""Le contraste des deux palettes, lu dans `web/src/index.css`.

Écrit en Python plutôt qu'en Vitest pour une raison de fond : ce test ne mesure pas un
composant, il mesure des **valeurs**. Il lit le fichier de style comme une donnée, sans
navigateur ni rendu — ce qui le rend aussi rapide que les autres et le fait tourner dans
la même suite que le reste des invariants.

Pourquoi il existe : une palette claire se dégrade en silence. Un jeton ajusté d'un cran
« pour faire joli » fait passer un texte secondaire sous le seuil de lisibilité, et rien
ne le signale — ni la compilation, ni les tests de composants, ni l'œil de qui a choisi
la teinte sur un bon écran. C'est le même mode de défaillance que le reste du projet
traque ailleurs : plausible, sans erreur, sans avertissement.

Il a d'ailleurs trouvé deux défauts **déjà livrés** dans le thème sombre au moment où il
a été écrit : un texte secondaire à 3,75:1 et, sur le bouton d'envoi, du blanc à 2,77:1.
"""

from __future__ import annotations

import pathlib
import re

import pytest

CSS = pathlib.Path(__file__).resolve().parent.parent / "web" / "src" / "index.css"

# Seuils WCAG 2.1 AA : 4.5 pour du texte courant, 3.0 pour un élément d'interface ou un
# glyphe qui ne porte pas d'information (la ponctuation d'une requête colorée).
TEXTE = 4.5
INTERFACE = 3.0

PAIRES = [
    ("texte", "fond", TEXTE),
    ("texte", "surface", TEXTE),
    ("texte-fort", "fond", TEXTE),
    ("texte-attenue", "fond", TEXTE),
    ("texte-faible", "fond", TEXTE),
    ("texte-faible", "surface", TEXTE),
    ("accent", "fond", TEXTE),
    ("accent", "surface", TEXTE),
    # Les deux rôles de l'accent, qui s'opposent : `accent` se lit **sur** la page,
    # `accent-fond` porte du texte clair **par-dessus** lui.
    ("sur-accent", "accent-fond", TEXTE),
    ("sur-accent", "accent-fond-survol", TEXTE),
    ("succes", "fond", INTERFACE),
    ("alerte", "fond", INTERFACE),
    ("erreur", "fond", INTERFACE),
    ("erreur", "erreur-fond", TEXTE),
    ("alerte", "alerte-fond", TEXTE),
    ("sql-motcle", "surface-appuyee", TEXTE),
    ("sql-fonction", "surface-appuyee", TEXTE),
    ("sql-chaine", "surface-appuyee", TEXTE),
    ("sql-nombre", "surface-appuyee", TEXTE),
    ("sql-operateur", "surface-appuyee", TEXTE),
    ("sql-ponctuation", "surface-appuyee", INTERFACE),
    ("sql-commentaire", "surface-appuyee", TEXTE),
    ("sql-variable", "surface-appuyee", TEXTE),
]


def _bloc(nom: str) -> dict[str, str]:
    """Les jetons d'un sélecteur. Lit le fichier réel, jamais une copie.

    Une copie des valeurs dans ce fichier ferait passer le test après que la palette a
    divergé — c'est-à-dire exactement quand il devrait échouer.
    """
    corps = re.search(rf"{nom}\s*\{{(.*?)\n\}}", CSS.read_text(), re.S)
    assert corps is not None, f"sélecteur {nom} introuvable dans index.css"
    return dict(re.findall(r"--jeton-([\w-]+):\s*(#[0-9a-fA-F]{6})", corps.group(1)))


def _palettes() -> dict[str, dict[str, str]]:
    clair = _bloc(r":root")
    # Le thème sombre ne redéfinit que ce qui change : il hérite du reste, comme dans le
    # navigateur. Le tester isolément mesurerait une palette qui n'existe pas.
    sombre = clair | _bloc(r':root\[data-theme="sombre"\]')
    return {"clair": clair, "sombre": sombre}


def _luminance(couleur: str) -> float:
    def canal(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (int(couleur[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def contraste(avant: str, arriere: str) -> float:
    a, b = _luminance(avant), _luminance(arriere)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@pytest.mark.parametrize("theme", ["clair", "sombre"])
@pytest.mark.parametrize("avant, arriere, seuil", PAIRES)
def test_le_contraste_tient_dans_les_deux_themes(
    theme: str, avant: str, arriere: str, seuil: float
) -> None:
    palette = _palettes()[theme]
    for jeton in (avant, arriere):
        assert jeton in palette, f"jeton `{jeton}` absent de la palette {theme}"

    mesure = contraste(palette[avant], palette[arriere])

    assert mesure >= seuil, (
        f"thème {theme} : `{avant}` sur `{arriere}` tombe à {mesure:.2f}:1 pour un seuil "
        f"de {seuil}:1 ({palette[avant]} sur {palette[arriere]}). Corriger la teinte dans "
        f"`web/src/index.css` — pas le seuil."
    )


def test_le_calcul_de_contraste_rougirait_sur_une_palette_delavee():
    """Contre-épreuve : sans elle, le test ci-dessus pourrait ne rien mesurer.

    Un blanc cassé sur blanc doit tomber très bas, et un noir sur blanc très haut. Si le
    calcul était faux — canaux inversés, gamma oublié — les deux se ressembleraient et
    toutes les assertions passeraient sur n'importe quelle palette.
    """
    assert contraste("#ffffff", "#000000") == pytest.approx(21.0, abs=0.1)
    assert contraste("#f8fafc", "#ffffff") < 1.1
    assert contraste("#94a3b8", "#f1f5f9") < TEXTE
