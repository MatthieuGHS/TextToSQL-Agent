"""Tests du point d'entrée de campagne. Aucun appel API, par construction et par garde.

Trois choses s'y jouent, et ce sont trois façons différentes de perdre de l'argent ou une
mesure :

1. une campagne réelle partie depuis la suite de tests ;
2. le jeu de contrôle ouvert par distraction, ce qui le transforme en jeu de réglage ;
3. un mode à blanc qui ne serait pas à blanc.
"""

from __future__ import annotations

import pytest

from src.db import connexion
from tests.eval import agent_reel
from tests.eval.__main__ import CONFIRMATION_CONTROLE, main


@pytest.fixture
def base_presente():
    try:
        connexion.ouvrir().close()
    except FileNotFoundError:
        pytest.skip("base absente : lancer `python -m src.etl.build_db`")


# --- 1. Une campagne réelle ne part jamais d'ici --------------------------------------


def test_une_campagne_reelle_est_refusee_depuis_la_suite():
    """Le garde-fou qui rend l'invariant mécanique plutôt que déclaratif.

    Ce module vit dans `tests/`. Sans ce contrôle, un `main([])` glissé dans un fichier de
    test partirait consommer une campagne entière à chaque exécution de la suite — y
    compris à chaque sauvegarde. Rien ne lèverait, et on ne le verrait que sur la facture.
    """
    with pytest.raises(RuntimeError, match="depuis la suite de tests"):
        main([])


# --- 2. Le scellé ---------------------------------------------------------------------


def test_le_jeu_de_controle_refuse_de_s_ouvrir_sans_phrase(capsys):
    """Exécuté avant la fin d'E8, le jeu de contrôle devient un jeu de réglage comme un
    autre — et il ne mesure plus rien. L'option existe, la phrase l'empêche par accident.
    """
    code = main(["--controle", "oui", "--a-blanc"])

    assert code == 2
    assert "sous scellé" in capsys.readouterr().err


def test_la_phrase_exacte_leve_le_refus(base_presente, tmp_path, monkeypatch):
    """Contre-épreuve : sans elle, le test précédent ne prouverait pas que l'option
    marche, seulement qu'elle refuse toujours."""
    from tests.eval import __main__ as lancement

    monkeypatch.setattr(lancement, "RACINE_EVAL", tmp_path)
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg00000": {"identifiant": "modele-x", '
        '"effort": "medium"}}, "derniere": "reg00000"}'
    )

    code = main(["--controle", CONFIRMATION_CONTROLE, "--a-blanc", "--source", "corpus"])

    # Aucune exécution : le cache est vide. Ce qui compte est que le refus soit levé.
    assert code == 1


# --- 3. Le mode à blanc est à blanc ---------------------------------------------------


def test_le_mode_a_blanc_n_appelle_jamais_le_modele(base_presente, tmp_path, monkeypatch):
    """Garanti par le type, pas par une intention.

    L'agent hors ligne porte les deux clés du cache et **lève si on l'appelle**. Une
    exécution absente du cache est donc ignorée ; elle ne peut pas partir en appel.
    """
    from tests.eval import __main__ as lancement

    monkeypatch.setattr(lancement, "RACINE_EVAL", tmp_path)
    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg00000": {"identifiant": "modele-x", '
        '"effort": "medium"}}, "derniere": "reg00000"}'
    )

    agent = agent_reel.hors_ligne(tmp_path, connexion.ouvrir())

    with pytest.raises(RuntimeError, match="ne devrait jamais arriver"):
        agent("une question")


def test_un_balayage_laisse_chaque_campagne_rejouable(base_presente, tmp_path):
    """Le registre garde une entrée par jeu de réglages, il n'écrase pas.

    Le balayage d'effort enchaîne trois campagnes. Avec une trace unique, la dernière
    effaçait les précédentes : la ligne de base devenait irrejouable à blanc alors que ses
    réponses étaient toujours en cache — et c'est précisément elle qu'on veut comparer.
    """
    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg-medium": {"identifiant": "m", "effort": "medium"}, '
        '"reg-high": {"identifiant": "m", "effort": "high"}}, "derniere": "reg-high"}'
    )
    con = connexion.ouvrir()
    try:
        assert agent_reel.hors_ligne(tmp_path, con).empreinte_reglages == "reg-high"
        assert (
            agent_reel.hors_ligne(tmp_path, con, "medium").empreinte_reglages
            == "reg-medium"
        )
        with pytest.raises(KeyError, match="aucune campagne à l'effort 'low'"):
            agent_reel.hors_ligne(tmp_path, con, "low")
    finally:
        con.close()


def test_un_effort_ambigu_leve_au_lieu_de_choisir(base_presente, tmp_path):
    """E8 fera varier `MAX_ITERATIONS` à effort constant : deux campagnes `medium`.

    L'ancienne résolution prenait la première trouvée, au hasard de l'ordre d'insertion
    du registre. Rejouer à blanc la mauvaise campagne ne lève rien et produit un rapport
    parfaitement plausible — la troisième occurrence de « ce qui n'est pas dans la clé se
    ressert ». Ce test échoue sur cette version-là.

    La porte de sortie est vérifiée dans le même geste : lever sans recours ne ferait
    que déplacer le problème.
    """
    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg-4-tours": {"identifiant": "m", "effort": "medium"}, '
        '"reg-8-tours": {"identifiant": "m", "effort": "medium"}}, '
        '"derniere": "reg-8-tours"}'
    )
    con = connexion.ouvrir()
    try:
        with pytest.raises(KeyError, match="2 campagnes à l'effort 'medium'"):
            agent_reel.hors_ligne(tmp_path, con, "medium")

        agent = agent_reel.hors_ligne(tmp_path, con, "reg-4-tours")
        assert agent.empreinte_reglages == "reg-4-tours"
    finally:
        con.close()


def test_l_option_campagne_atteint_le_runner(base_presente, tmp_path, monkeypatch):
    """Le câblage, et non plus seulement `hors_ligne()`.

    `--campagne` a été ajoutée pour départager deux campagnes de même effort. Elle passe
    par `args.campagne or args.effort` dans `main()` : une inversion de ces deux termes
    rendrait l'option inopérante sans rien lever.

    **Les deux options sont passées ensemble**, et c'est ce qui rend le test capable
    d'échouer. Avec `--campagne` seule, `args.effort` vaut `None` et les deux ordres
    donnent le même résultat — première version de ce test, qui passait aussi bien sur le
    code inversé. Le seul cas discriminant est celui pour lequel l'option existe : un
    effort qui ne désigne plus une campagne unique, et une empreinte pour trancher.
    """
    from tests.eval import runner
    from tests.eval import __main__ as lancement

    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg-4-tours": {"identifiant": "m", "effort": "medium"}, '
        '"reg-8-tours": {"identifiant": "m", "effort": "medium"}}, '
        '"derniere": "reg-8-tours"}'
    )
    monkeypatch.setattr(lancement, "RACINE_EVAL", tmp_path)

    vus = []
    monkeypatch.setattr(
        runner, "executer", lambda cas, agent, con, **kw: vus.append(agent) or []
    )

    main(["--a-blanc", "--effort", "medium", "--campagne", "reg-4-tours",
          "--source", "corpus"])

    assert vus and vus[0].empreinte_reglages == "reg-4-tours"


def test_le_mode_a_blanc_le_dit_quand_il_n_a_rien_a_rejouer(base_presente, tmp_path,
                                                            monkeypatch):
    """Un cache vide doit se dire, pas produire un rapport à zéro qu'on lirait comme
    une mesure."""
    from tests.eval import __main__ as lancement

    monkeypatch.setattr(lancement, "RACINE_EVAL", tmp_path)

    with pytest.raises(FileNotFoundError, match="aucune campagne réelle"):
        main(["--a-blanc"])


def test_l_empreinte_hors_ligne_est_celle_du_prompt_courant(base_presente, tmp_path):
    """C'est ce qui périme le cache tout seul.

    Si le prompt bouge, l'empreinte change, les clés ne répondent plus et le rapport le
    dit — plutôt que de comparer en silence des réponses produites sous deux prompts.
    """
    from src.agent import prompt

    (tmp_path / agent_reel.FICHIER_MODELE).write_text(
        '{"campagnes": {"reg00000": {"identifiant": "modele-x", '
        '"effort": "high"}}, "derniere": "reg00000"}'
    )
    con = connexion.ouvrir()
    try:
        agent = agent_reel.hors_ligne(tmp_path, con)

        assert agent.empreinte_prompt == prompt.empreinte(prompt.construire(con))
        assert agent.identifiant == "modele-x"
        # Sans lui, un rapport à blanc perdrait le réglage sous lequel la campagne a
        # été jouée — donc son opposabilité à toute autre mesure.
        assert agent.effort == "high"
    finally:
        con.close()
