import { describe, expect, it } from 'vitest'
import { donneesDe } from '../src/composants/Graphique'
import type { Graphique } from '../src/types'

/**
 * Ce composant ne décide de rien : le type, l'axe et la répartition sur deux axes sont
 * choisis côté serveur et testés là-bas, en fonctions pures. Ce qui reste à vérifier ici
 * est la transposition — et le fait qu'elle ne comble pas les trous.
 */
const COURBE: Graphique = {
  type: 'courbe',
  x: 'step_date',
  etiquettes: ['2024-01-01', '2024-01-08', '2024-01-15'],
  series: [
    { colonne: 'cost', valeurs: [100, null, 300], axe_secondaire: false },
    { colonne: 'mes', valeurs: [10, 20, 30], axe_secondaire: true },
  ],
}

describe('données du graphique', () => {
  it('transpose les colonnes en points', () => {
    const points = donneesDe(COURBE)

    expect(points).toHaveLength(3)
    expect(points[0]).toEqual({ x: '01/01/24', cost: 100, mes: 10 })
    expect(points[2]).toEqual({ x: '15/01/24', cost: 300, mes: 30 })
  })

  it('garde les trous, sans les combler', () => {
    // `cost` est NULL sur tout le SEO : non acheté, ce qui n'est pas gratuit. Un zéro
    // dessinerait une chute qui n'a pas eu lieu, et un graphique rend cette erreur
    // convaincante. La courbe doit s'interrompre.
    const points = donneesDe(COURBE)

    expect(points[1].cost).toBeNull()
    expect(points[1].cost).not.toBe(0)
  })

  it('abrège les dates ISO, laisse les catégories intactes', () => {
    const barres: Graphique = {
      type: 'barres',
      x: 'channel',
      etiquettes: ['tv', 'sea'],
      series: [{ colonne: 'cost', valeurs: [1, 2], axe_secondaire: false }],
    }

    expect(donneesDe(barres).map((p) => p.x)).toEqual(['tv', 'sea'])
    expect(donneesDe(COURBE)[0].x).toBe('01/01/24')
  })
})
