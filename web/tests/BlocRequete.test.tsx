import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BlocRequete } from '../src/composants/BlocRequete'
import type { Requete } from '../src/types'

/**
 * Les tables lues sont affichées **sans qu'on ait à déplier** : le client veut les voir
 * d'un coup d'œil, et ce jeu de données fait cohabiter deux périmètres aux ordres de
 * grandeur voisins — `media` est l'annonceur seul, `contexte` le marché entier. Savoir
 * laquelle a répondu est ce qui distingue un chiffre juste d'un chiffre crédible.
 *
 * La liste vient du serveur, où elle est décidée par le parseur du moteur. Rien ici ne
 * la recalcule, et ces tests garantissent qu'on n'a pas cédé à la tentation : un nom de
 * table peut vivre dans un littéral SQL.
 */
const REQUETE: Requete = {
  sql: "SELECT SUM(cost) FROM media WHERE channel = 'contexte'",
  colonnes: ['somme'],
  lignes: [[1250]],
  tronque: false,
  duree_ms: 3,
  erreur: null,
  tables: ['media'],
  raisonnement: 'Je somme le coût sur le périmètre annonceur.',
  graphique: null,
}

describe('bloc de requête', () => {
  it('affiche les tables lues sans qu on déplie', () => {
    render(<BlocRequete requete={REQUETE} />)

    expect(screen.getByText('media')).toBeInTheDocument()
  })

  it("n'affiche que ce que le serveur a décidé, pas ce que le SQL contient", () => {
    // `contexte` est dans un littéral de cette requête, et le serveur ne l'a pas retenu.
    // Le voir apparaître voudrait dire qu'une extraction textuelle est revenue côté
    // navigateur — la réponse dirait « issue du marché » sur un chiffre de l'annonceur.
    render(<BlocRequete requete={REQUETE} />)

    expect(screen.queryByText('contexte')).not.toBeInTheDocument()
  })

  it('affiche chaque table d une jointure', () => {
    render(
      <BlocRequete
        requete={{ ...REQUETE, tables: ['kpi_compteurs', 'media'] }}
      />,
    )

    expect(screen.getByText('kpi_compteurs')).toBeInTheDocument()
    expect(screen.getByText('media')).toBeInTheDocument()
  })

  it("affiche le raisonnement une fois le bloc déplié", () => {
    // Écrit par le modèle avant d'exécuter, donc au-dessus du SQL : il explique la
    // requête qu'on s'apprête à lire, il ne la justifie pas après coup.
    render(<BlocRequete requete={REQUETE} />)
    fireEvent.click(screen.getByText(/^requête$/i))

    expect(
      screen.getByText('Je somme le coût sur le périmètre annonceur.'),
    ).toBeInTheDocument()
  })

  it("n'affiche rien quand le raisonnement est absent", () => {
    // Les réponses produites avant que le champ n'existe le rendent vide : le bloc ne
    // doit pas ouvrir un encart vide pour autant.
    render(<BlocRequete requete={{ ...REQUETE, raisonnement: '' }} />)
    fireEvent.click(screen.getByText(/^requête$/i))

    expect(screen.queryByText(/Je somme le coût/)).not.toBeInTheDocument()
  })

  it("n'affiche aucune table sur une requête en échec", () => {
    // Le serveur rend une liste vide : une erreur de syntaxe n'a lu aucune table. Le
    // bloc ne doit pas inventer d'en-tête vide pour autant.
    const { container } = render(
      <BlocRequete
        requete={{ ...REQUETE, erreur: 'Colonne inconnue', tables: [] }}
      />,
    )

    expect(screen.getByText(/requête en échec/i)).toBeInTheDocument()
    expect(container.querySelectorAll('.font-mono.text-\\[11px\\]')).toHaveLength(0)
  })
})
