import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from '../src/composants/Markdown'

/**
 * Le texte de l'agent est du Markdown dans 98 % des cas (mesuré sur les 171 exécutions du
 * cache d'évaluation). Ces tests couvrent les deux choses qui comptent : qu'il soit rendu,
 * et qu'il ne le soit pas trop — c'est-à-dire que rien d'exécutable ne passe.
 */
describe('rendu du Markdown', () => {
  it('interprète le gras et le code en ligne', () => {
    render(<Markdown texte="Le total est de **192 M€** dans `media.cost`." />)

    expect(screen.getByText('192 M€').tagName).toBe('STRONG')
    expect(screen.getByText('media.cost').tagName).toBe('CODE')
  })

  it('rend un tableau Markdown comme un vrai tableau', () => {
    // 19 % des réponses en cache en contiennent : sans `remark-gfm` elles s'affichaient
    // en tuyaux bruts, ce qui était le pire cas visible.
    const { container } = render(
      <Markdown texte={'| canal | coût |\n|---|---|\n| tv | 100 |'} />,
    )

    expect(container.querySelector('table')).not.toBeNull()
    expect(screen.getByText('canal').tagName).toBe('TH')
    expect(screen.getByText('tv').tagName).toBe('TD')
  })

  it("n'exécute pas le HTML brut contenu dans une réponse", () => {
    // La seule propriété de sécurité de cette page. Le texte vient d'un modèle, qui lit
    // lui-même la base : une chaîne malveillante stockée dans les données pourrait
    // ressortir dans une réponse. React Markdown ne l'interprète pas — sauf si on ajoute
    // `rehype-raw`, ce que ce test interdit de faire par distraction.
    const { container } = render(
      <Markdown texte={'Résultat <img src=x onerror="alert(1)"> et <b>gras</b>.'} />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">')
  })

  it('ouvre les liens ailleurs, sans donner la main sur l’onglet d’origine', () => {
    const { container } = render(<Markdown texte="[doc](https://exemple.test)" />)
    const lien = container.querySelector('a')

    expect(lien?.getAttribute('target')).toBe('_blank')
    expect(lien?.getAttribute('rel')).toContain('noopener')
  })
})
