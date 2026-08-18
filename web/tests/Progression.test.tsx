import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Progression } from '../src/composants/Progression'
import type { Etape } from '../src/types'

const ETAPES: Etape[] = [
  { type: 'reflexion', tour: 1, sur: 4 },
  { type: 'requete', sql: 'SELECT 1', lignes: 3, duree_ms: 12, erreur: null },
  { type: 'redaction' },
]

describe('progression', () => {
  it('rend chaque étape, en direct comme après coup', () => {
    // Les étapes survivent à la réponse : elles disent en combien de tours et dans quel
    // ordre, ce que la liste finale des requêtes ne dit pas.
    const { rerender } = render(<Progression etapes={ETAPES} />)
    expect(screen.getByText(/3 lignes · 12 ms/)).toBeInTheDocument()

    rerender(<Progression etapes={ETAPES} terminee />)
    expect(screen.getByText(/3 lignes · 12 ms/)).toBeInTheDocument()
    expect(screen.getByText(/Rédaction/)).toBeInTheDocument()
  })

  it('cesse de se signaler une fois terminée', () => {
    // Sans ce basculement, une conversation de cinq questions afficherait cinq pastilles
    // clignotantes pour des exécutions terminées depuis longtemps.
    const { container, rerender } = render(<Progression etapes={ETAPES} />)
    expect(container.querySelector('.animate-pulse')).not.toBeNull()

    rerender(<Progression etapes={ETAPES} terminee />)
    expect(container.querySelector('.animate-pulse')).toBeNull()
  })

  it('distingue un tâtonnement des étapes réussies', () => {
    const echec: Etape[] = [
      { type: 'requete', sql: 'SELECT x', lignes: 0, duree_ms: 2, erreur: 'colonne absente' },
    ]
    const { container } = render(<Progression etapes={echec} terminee />)

    expect(screen.getByText(/Requête refusée/)).toBeInTheDocument()
    expect(container.querySelector('.bg-red-500')).not.toBeNull()
  })
})
