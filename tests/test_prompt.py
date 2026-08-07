"""Tests du prompt système.

Un prompt est d'ordinaire un artefact que personne ne teste. Celui-ci l'est, parce que sa
partie générée est **vérifiable contre sa source** : chaque valeur qu'il énonce doit
exister dans la base, et chaque valeur de la base doit y figurer.

Quatre familles :

1. **Cohérence** — le prompt n'invente rien et n'omet rien. C'est la garantie
   anti-péremption : ces tests échoueraient au lendemain d'un rafraîchissement de
   l'extrait, avant que l'agent ne se mette à affirmer des choses fausses.
2. **Stabilité** — le prompt est le préfixe mis en cache ; un octet qui change d'une
   exécution à l'autre coûterait dix fois le prix, sans erreur ni avertissement.
3. **Exemples** — un exemple faux enseigne une erreur.
4. **Non-scriptage** — le prompt décrit les données, il ne répond pas à des questions
   connues d'avance.
"""

from __future__ import annotations

import re

import duckdb
import pytest

from src.agent import prompt
from src.agent.prompt import schema
from src.db import connexion, sql


@pytest.fixture(scope="module")
def con():
    try:
        c = connexion.ouvrir()
    except FileNotFoundError:
        pytest.skip("base absente : lancer `python -m src.etl.build_db`")
    yield c
    c.close()


@pytest.fixture(scope="module")
def texte(con) -> str:
    return prompt.construire(con)


def valeurs(con: duckdb.DuckDBPyConnection, table: str, colonne: str) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            f"SELECT DISTINCT {colonne} FROM {table} WHERE {colonne} IS NOT NULL"
        ).fetchall()
    ]


# --- 1. Cohérence avec la base --------------------------------------------------------


@pytest.mark.parametrize("colonne", schema.COLONNES_ENUMEREES)
def test_toutes_les_valeurs_de_la_base_sont_annoncees(con, texte, colonne):
    """Le prompt n'omet rien.

    C'est le test qui protège de la péremption : si un extrait futur ajoute un canal, il
    échoue ici — avant que l'agent n'affirme de bonne foi que ce canal n'existe pas.
    """
    presentes = valeurs(con, "media", colonne)
    if len(presentes) > schema.SEUIL_ENUMERATION:
        pytest.skip(f"{colonne} n'est pas énumérée (trop de valeurs)")

    manquantes = [v for v in presentes if v not in texte]

    assert not manquantes, f"absentes du prompt : {manquantes}"


def test_le_prompt_n_invente_aucun_canal(con, texte):
    """Le prompt n'invente rien.

    On extrait la ligne des canaux telle qu'elle est écrite et on la confronte à la base :
    une valeur en trop serait une affirmation fausse, plus grave qu'une omission.
    """
    ligne = next(l for l in texte.splitlines() if l.startswith("- `channel`"))
    annonces = set(re.search(r": (.+)$", ligne).group(1).split(", "))

    assert annonces == set(valeurs(con, "media", "channel"))


def test_les_metriques_de_contexte_sont_completes(con, texte):
    manquantes = [m for m in valeurs(con, "contexte", "metric") if f"`{m}`" not in texte]

    assert not manquantes, f"métriques de contexte absentes : {manquantes}"


def test_les_bornes_temporelles_sont_exactes(con, texte):
    """Une borne fausse ferait calculer toutes les dates relatives de travers."""
    for table in schema.TABLES:
        debut, fin = con.execute(
            f"SELECT MIN(step_date)::DATE, MAX(step_date)::DATE FROM {table}"
        ).fetchone()

        assert f"{debut} → {fin}" in texte, f"bornes de {table} absentes ou fausses"


def test_les_couples_canal_metrique_sont_exacts(con, texte):
    """Chaque canal n'a qu'une métrique : c'est une propriété structurelle des données."""
    for metric, canaux in con.execute(
        "SELECT performance_metric, STRING_AGG(DISTINCT channel, ', ' ORDER BY channel) "
        "FROM media GROUP BY 1"
    ).fetchall():

        assert f"`{metric}` : {canaux}" in texte


def test_l_annonceur_est_nomme(con, texte):
    """Sans lui, rien ne distingue nos un montant à sept chiffres de ceux d'un concurrent dans `contexte`."""
    (annonceur,) = valeurs(con, "media", "brand_name")

    assert f"`{annonceur}`" in texte


def test_les_colonnes_a_forte_cardinalite_sont_signalees(texte):
    """Le modèle doit savoir qu'il regarde un extrait, pas une liste complète."""
    assert "`support` — N valeurs distinctes" in texte
    assert "SELECT DISTINCT" in texte


# --- 2. Stabilité (le préfixe est mis en cache) ---------------------------------------


def test_deux_constructions_donnent_les_memes_octets(con):
    """Un préfixe instable ne serait jamais mis en cache — sans erreur, à dix fois le prix.

    `SELECT DISTINCT` ne garantit aucun ordre : sans `ORDER BY`, ce test échouerait
    par intermittence.
    """
    assert prompt.construire(con) == prompt.construire(con)


def test_aucun_element_variable_dans_le_prompt(texte):
    """Une date du jour ou un identifiant aléatoire invaliderait le cache à chaque appel.

    Elle serait de surcroît fausse : les dates relatives se calculent depuis la fin des
    données, pas depuis aujourd'hui.
    """
    import datetime

    aujourdhui = datetime.date.today()

    assert str(aujourdhui) not in texte
    assert str(aujourdhui.year) not in texte.split("Période couverte")[0]


def test_l_empreinte_est_stable_et_discriminante(texte):
    assert prompt.empreinte(texte) == prompt.empreinte(texte)
    assert prompt.empreinte(texte) != prompt.empreinte(texte + " ")


# --- 3. Les exemples s'exécutent ------------------------------------------------------


def sql_des_exemples() -> list[str]:
    contenu = (prompt.DOSSIER / "exemples.md").read_text(encoding="utf-8")
    return [b.strip() for b in re.findall(r"```sql\n(.+?)```", contenu, re.S)]


def test_il_y_a_bien_des_exemples():
    assert 3 <= len(sql_des_exemples()) <= 5


@pytest.mark.parametrize("requete", sql_des_exemples())
def test_chaque_exemple_s_execute_et_renvoie_des_lignes(con, requete):
    """Un exemple qui échoue enseigne une erreur ; un exemple vide enseigne le doute."""
    resultat = sql.run_sql(requete, con)

    assert len(resultat) > 0, "exemple sans résultat : il paraîtrait cassé"


@pytest.mark.parametrize("requete", sql_des_exemples())
def test_aucun_exemple_n_utilise_la_date_du_jour(requete):
    """Les exemples enseignent une forme : celle-ci doit être la bonne."""
    haut = requete.upper()

    assert "CURRENT_DATE" not in haut and "NOW(" not in haut


def test_les_exemples_montrent_la_forme_qui_evite_le_produit_cartesien(con):
    """L'exemple de jointure doit agréger avant de joindre.

    Vérifié sur les valeurs : joindre `media` et `contexte` directement sur `step_date`
    double les totaux, sans qu'aucun message ne le signale. L'exemple doit donc donner le
    même résultat qu'une agrégation faite séparément.
    """
    jointure = next(q for q in sql_des_exemples() if "JOIN" in q.upper())
    obtenu = {r[0]: float(r[1]) for r in sql.run_sql(jointure, con).lignes}

    attendu = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT YEAR(step_date), ROUND(SUM(performance)) FROM media "
            "WHERE performance_metric = 'grp' GROUP BY 1"
        ).fetchall()
    }

    for annee, valeur in obtenu.items():
        assert valeur == attendu[annee], f"totaux multipliés sur {annee}"


# --- 4. Non-scriptage -----------------------------------------------------------------


def test_aucune_reference_a_une_question(texte):
    """« Si on te demande X, réponds Y » ferait passer cette question-là et échouer la
    suivante. Le prompt décrit les données, il ne répond pas d'avance."""
    interdits = [
        r"question\s*n?°?\s*\d",
        r"\bQ\d+\b",
        r"si on te demande",
        r"si l'utilisateur demande",
        r"grille",
    ]
    trouves = [m for m in interdits if re.search(m, texte, re.I)]

    assert not trouves, f"formulations de scriptage : {trouves}"


def test_aucune_valeur_inexistante_n_est_nommee(texte):
    """Nommer un piège (« TikTok n'existe pas ») réglerait un cas et laisserait les autres.

    Le prompt donne la liste exhaustive des canaux : le modèle en déduit lui-même l'absence
    de n'importe quelle valeur, y compris celles auxquelles nous n'avons pas pensé.
    """
    for valeur in ("tiktok", "snapchat", "twitch", "netflix"):
        assert valeur not in texte.lower()


def test_le_prompt_est_au_dessus_du_seuil_de_cache(texte):
    """Approximation prudente, la mesure exacte est faite hors tests (elle coûte un appel).

    Le seuil de Sonnet 5 est de 1 024 tokens. Un caractère valant au plus un token, un
    prompt de plus de 4 096 caractères est nécessairement au-dessus.
    """
    assert len(texte) > 4096, f"{len(texte)} caractères — risque de passer sous le seuil"
