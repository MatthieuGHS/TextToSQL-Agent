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


@pytest.fixture(scope="module")
def genere(con) -> str:
    """La seule partie que la base est censée garantir.

    Les tests de cohérence portent sur elle et non sur le prompt entier : chercher une
    valeur dans le texte complet la trouverait aussi dans un fichier écrit à la main, et
    le test resterait vert alors même que ce fichier serait devenu faux.
    """
    return schema.generer(con)


# Longueur minimale d'un préfixe mis en cache, en tokens. Elle dépend du modèle et n'est
# pas monotone d'une génération à l'autre (512, 1 024, 2 048 ou 4 096 selon le modèle) :
# cette constante appartient donc au choix de modèle et devra le suivre quand E4 le figera.
SEUIL_CACHE_TOKENS = 1024  # claude-sonnet-5

# Un token vaut *au plus* ~4 caractères sur du français mêlé de Markdown et de SQL. C'est
# bien ce sens-là qu'il faut : « un caractère vaut au plus un token » majore le nombre de
# tokens et ne prouverait donc rien sur un plancher.
CARACTERES_PAR_TOKEN = 4


def valeurs(con: duckdb.DuckDBPyConnection, table: str, colonne: str) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            f"SELECT DISTINCT {colonne} FROM {table} WHERE {colonne} IS NOT NULL"
        ).fetchall()
    ]


# --- 1. Cohérence avec la base --------------------------------------------------------


@pytest.mark.parametrize("colonne", schema.COLONNES_ENUMEREES)
def test_toutes_les_valeurs_de_la_base_sont_annoncees(con, genere, colonne):
    """Le prompt n'omet rien.

    C'est le test qui protège de la péremption : si un extrait futur ajoute un canal, il
    échoue ici — avant que l'agent n'affirme de bonne foi que ce canal n'existe pas.

    Il porte sur la partie **générée** seulement. Cherché dans le prompt entier, il
    passerait au vert dès qu'un fichier écrit à la main mentionne la valeur au détour
    d'une phrase — c'est-à-dire précisément quand la section générée a cessé de faire
    son travail.
    """
    presentes = valeurs(con, "media", colonne)
    if len(presentes) > schema.SEUIL_ENUMERATION:
        pytest.skip(f"{colonne} n'est pas énumérée (trop de valeurs)")

    manquantes = [v for v in presentes if v not in genere]

    assert not manquantes, f"absentes de la partie générée : {manquantes}"


@pytest.mark.parametrize("colonne", schema.COLONNES_ENUMEREES)
def test_les_enumerations_sont_triees(con, genere, colonne):
    """Le tri est ce qui rend le préfixe reproductible, donc cacheable.

    C'est le test qui manquait : comparer deux constructions successives ne prouve rien,
    parce que dans un même processus DuckDB rendra très probablement le même ordre même
    sans `ORDER BY`. Celui-ci échoue si on retire le tri.
    """
    prefixe = f"- `{colonne}` ("
    ligne = next((l for l in genere.splitlines() if l.startswith(prefixe)), None)
    if ligne is None:
        pytest.skip(f"{colonne} n'est pas énumérée")

    annoncees = re.search(r": (.+)$", ligne).group(1).split(", ")

    assert annoncees == sorted(annoncees)


def test_le_prompt_n_invente_aucun_canal(con, texte):
    """Le prompt n'invente rien.

    On extrait la ligne des canaux telle qu'elle est écrite et on la confronte à la base :
    une valeur en trop serait une affirmation fausse, plus grave qu'une omission.
    """
    ligne = next(l for l in texte.splitlines() if l.startswith("- `channel`"))
    annonces = set(re.search(r": (.+)$", ligne).group(1).split(", "))

    assert annonces == set(valeurs(con, "media", "channel"))


def test_les_metriques_de_contexte_sont_completes(con, genere):
    manquantes = [m for m in valeurs(con, "contexte", "metric") if f"`{m}`" not in genere]

    assert not manquantes, f"métriques de contexte absentes : {manquantes}"


def test_le_tableau_des_homonymes_reste_complet(con):
    """Le piège n° 1 du jeu de données, gardé par un test plutôt que par la vigilance.

    Le tableau est écrit à la main parce que `compteurs` ne se dérive pas — le lien passe
    par le fait qu'une table nommée `kpi_compteurs` compte des compteurs, ce qu'aucune
    requête ne dira. Mais la *part dérivable* se vérifie : toute variable de `contexte`
    qui porte le nom d'une colonne, d'une table ou d'une métrique de performance des deux
    autres tables est une collision, et doit figurer au tableau.

    Ce test passe au rouge le jour où un rafraîchissement introduit une quatrième
    collision — c'est-à-dire au moment exact où le piège s'aggrave sans prévenir.
    """
    metriques = set(valeurs(con, "contexte", "metric"))
    noms_ailleurs = (
        {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM duckdb_columns() "
                "WHERE table_name IN ('media', 'kpi_compteurs')"
            ).fetchall()
        }
        | set(valeurs(con, "media", "performance_metric"))
        | {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    )

    non_signalees = (metriques & noms_ailleurs) - set(schema.HOMONYMES)

    assert not non_signalees, (
        f"homonymies entre tables non signalées au modèle : {sorted(non_signalees)}. "
        f"Une somme entre tables sur ces variables donnerait un résultat faux mais "
        f"crédible."
    )
    assert set(schema.HOMONYMES) <= metriques, (
        f"le tableau annonce des variables de contexte qui n'existent plus : "
        f"{sorted(set(schema.HOMONYMES) - metriques)}"
    )


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


def test_l_annonceur_est_nomme(con, genere):
    """Sans lui, rien ne distingue les investissements suivis de ceux d'un concurrent
    présent dans `contexte`, dont les ordres de grandeur sont voisins."""
    (annonceur,) = valeurs(con, "media", "brand_name")

    assert f"`{annonceur}`" in genere


def test_les_colonnes_a_forte_cardinalite_sont_signalees(con, genere):
    """Le modèle doit savoir qu'il regarde un extrait, pas une liste complète.

    Le compte est lu dans la base, jamais écrit en dur : asserter une volumétrie ferait
    d'un rafraîchissement légitime une panne de suite de tests, alors que rien n'est
    cassé. C'est la même distinction invariant / volumétrie que côté ETL.
    """
    for colonne in ("support", "type"):
        (n,) = con.execute(f"SELECT COUNT(DISTINCT {colonne}) FROM media").fetchone()

        assert f"`{colonne}` — {n} valeurs distinctes" in genere

    assert "SELECT DISTINCT" in genere


# --- 1 bis. Cohérence des affirmations écrites à la main ------------------------------
#
# La partie générée ne peut pas mentir : elle vient de la base. La partie écrite, si —
# et c'est elle que personne ne relit après un rafraîchissement. Ces tests couvrent les
# affirmations de `metier.md` qu'une requête peut trancher.


def test_l_affirmation_sur_le_canal_sans_cout_reste_vraie(con):
    """`metier.md` explique au modèle qu'un seul canal est `owned`, ce qui justifie que
    son coût soit NULL sans être gratuit.

    Si un extrait futur en ajoute un second, la phrase devient fausse et le modèle
    expliquera de travers un NULL qui n'a plus le même sens.
    """
    owned = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT channel FROM media WHERE category = 'owned' ORDER BY 1"
        ).fetchall()
    ]
    metier = (prompt.DOSSIER / "metier.md").read_text(encoding="utf-8")

    assert len(owned) == 1, f"metier.md n'en décrit qu'un seul, la base en a {owned}"
    assert f"`{owned[0]}`" in metier


def test_metier_ne_decrit_aucune_variable_de_contexte_inexistante(con):
    """L'inverse du test de complétude, et le plus grave des deux.

    Un prompt qui omet une variable rend le modèle aveugle ; un prompt qui en invente une
    le rend affirmatif à tort — il construira un `WHERE metric = …` qui ne renvoie rien
    et pourra conclure à une absence dans les données.
    """
    reelles = set(valeurs(con, "contexte", "metric")) | set(
        valeurs(con, "contexte", "brand_name")
    )
    metier = (prompt.DOSSIER / "metier.md").read_text(encoding="utf-8")
    section = metier.split("**Variables de `contexte`.**")[1].split("## ")[0]
    citees = set(re.findall(r"`([a-z_0-9]+)`", section))

    assert citees <= reelles, (
        f"décrites dans metier.md mais absentes de la base : {sorted(citees - reelles)}"
    )


# --- 2. Stabilité (le préfixe est mis en cache) ---------------------------------------


def test_deux_constructions_donnent_les_memes_octets(con):
    """Un préfixe instable ne serait jamais mis en cache — sans erreur, à dix fois le prix.

    Sur **deux connexions distinctes**, et non deux appels sur la même : le cache est une
    correspondance d'octets entre deux exécutions du programme, pas entre deux lignes
    d'une même fonction. La garantie de fond est ailleurs, dans
    `test_les_enumerations_sont_triees` — celui-ci n'en est que le contrôle de bout en bout.
    """
    autre = connexion.ouvrir()
    try:
        assert prompt.construire(con) == prompt.construire(autre)
    finally:
        autre.close()


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
    """Approximation prudente ; la mesure exacte est faite hors tests, elle coûte un appel
    API — et le comptage du connecteur a ses propres pièges (voir `docs/decisions.md`).

    Le raisonnement va dans ce sens et pas dans l'autre : c'est parce qu'un token vaut au
    plus ~4 caractères qu'un texte assez long est nécessairement assez riche en tokens.
    Partir de « un caractère vaut au plus un token » majorerait le nombre de tokens, ce
    qui ne dirait rien d'un plancher.
    """
    plancher = SEUIL_CACHE_TOKENS * CARACTERES_PAR_TOKEN

    assert len(texte) > plancher, (
        f"{len(texte)} caractères pour un plancher de {plancher} — le préfixe risque de "
        f"passer sous le seuil de mise en cache et d'être repayé plein tarif."
    )
