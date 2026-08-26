import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { Entete } from '../src/composants/Entete'
import { Graphique } from '../src/composants/Graphique'
import { FournisseurTheme, themeRetenu } from '../src/theme'
import type { Graphique as TypeGraphique } from '../src/types'

const SANTE = {
  base_presente: true,
  cle_chargee: true,
  tables: ['media'],
  modele: 'claude-sonnet-5',
}

function entete() {
  return (
    <FournisseurTheme>
      <Entete sante={SANTE} erreur={null} page="conversation" onPage={() => {}} />
    </FournisseurTheme>
  )
}

describe('thème', () => {
  beforeEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('démarre en clair, comme le client l a demandé', () => {
    render(entete())

    expect(themeRetenu()).toBe('clair')
    expect(document.documentElement.dataset.theme).toBe('clair')
  })

  it('bascule, et retient le choix', () => {
    render(entete())

    fireEvent.click(screen.getByLabelText(/thème sombre/i))

    expect(document.documentElement.dataset.theme).toBe('sombre')
    // Retenu : sinon le script d'amorçage de `index.html` repartirait en clair au
    // rechargement, et la bascule ne vaudrait que pour l'onglet courant.
    expect(themeRetenu()).toBe('sombre')
  })

  it('repart du choix retenu plutôt que du défaut', () => {
    localStorage.setItem('theme', 'sombre')

    render(entete())

    expect(document.documentElement.dataset.theme).toBe('sombre')
  })

  it('propose de revenir au clair une fois en sombre', () => {
    // L'icône désigne la destination, pas l'état courant : une bascule qui afficherait
    // toujours la même icône se lit comme une décoration.
    render(entete())
    fireEvent.click(screen.getByLabelText(/thème sombre/i))

    expect(screen.getByLabelText(/thème clair/i)).toBeInTheDocument()
  })
})

const GRAPHIQUE: TypeGraphique = {
  type: 'courbe',
  x: 'step_date',
  etiquettes: ['2024-09-02', '2024-09-09'],
  series: [{ colonne: 'cost', valeurs: [1000, 250], axe_secondaire: false }],
  variantes: [],
  empilable: false,
}

describe('palette des graphiques', () => {
  // Recharts mesure son conteneur par `ResizeObserver`, que jsdom ne fournit pas : sans
  // lui le conteneur reste à zéro, aucun tracé n'est peint, et le test ne mesurerait
  // rien. On lui donne donc une surface. C'est la seule raison de ce bricolage — aucune
  // règle du composant n'est contournée, et c'est ce qui permet de vérifier la couleur
  // sur le SVG réellement produit plutôt que sur une intention.
  beforeAll(() => {
    globalThis.ResizeObserver = class {
      rappel: ResizeObserverCallback
      constructor(rappel: ResizeObserverCallback) {
        this.rappel = rappel
      }
      observe(cible: Element) {
        this.rappel(
          [{ target: cible, contentRect: { width: 640, height: 320 } }] as never,
          this as never,
        )
      }
      unobserve() {}
      disconnect() {}
    } as never
  })

  beforeEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('repeint les séries à la bascule, malgré la mémoïsation', () => {
    // **Le piège que ce test existe pour attraper.** `Graphique` est `memo`, et Recharts
    // veut des couleurs littérales — donc la palette ne peut pas venir de la feuille de
    // style. Si elle venait d'une constante de module, la bascule ne changerait aucune
    // prop, `memo` court-circuiterait le rendu, et les courbes resteraient dans la
    // palette sombre sur une page devenue blanche. C'est le contexte qui traverse `memo`.
    const { container } = render(
      <FournisseurTheme>
        <Entete sante={SANTE} erreur={null} page="conversation" onPage={() => {}} />
        <Graphique graphique={GRAPHIQUE} />
      </FournisseurTheme>,
    )

    const traits = () =>
      [...container.querySelectorAll('.recharts-line-curve, .recharts-curve')]
        .map((p) => p.getAttribute('stroke'))
        .filter((s): s is string => !!s && s !== 'none')

    const clair = traits()
    fireEvent.click(screen.getByLabelText(/thème sombre/i))
    const sombre = traits()

    expect(clair.length).toBeGreaterThan(0)
    expect(sombre.length).toBe(clair.length)
    expect(sombre).not.toEqual(clair)
  })
})
