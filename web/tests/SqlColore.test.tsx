import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SqlColore } from '../src/composants/SqlColore'

describe('coloration du SQL', () => {
  it('affiche la requête intégrale, à la lettre près', () => {
    // La propriété qui prime sur la couleur. Un coloriseur qui perdrait un fragment de
    // requête serait pire que pas de couleur du tout : la requête affichée est ce qui
    // rend la réponse vérifiable, et une requête tronquée mentirait sur ce qui a été
    // exécuté.
    const sql =
      "SELECT SUM(cost) AS total -- commentaire\nFROM media\nWHERE channel = 'tv' " +
      'AND step_date > DATE \'2024-01-01\''

    const { container } = render(<SqlColore sql={sql} />)

    expect(container.textContent).toBe(sql)
  })

  it('distingue les mots-clés, les chaînes et les nombres', () => {
    const { container } = render(<SqlColore sql="SELECT 42 FROM t WHERE a = 'x'" />)

    const classes = [...container.querySelectorAll('span')].map((s) => s.className)
    expect(classes.some((c) => c.includes('violet'))).toBe(true)  // mots-clés
    expect(classes.some((c) => c.includes('emerald'))).toBe(true) // nombres
    expect(classes.some((c) => c.includes('amber'))).toBe(true)   // chaînes
  })

  it("n'injecte aucun HTML brut, même si la requête en contient", () => {
    // Le SQL affiché est écrit par le modèle, à partir d'une question qui vient de
    // l'utilisateur. Passer par `Prism.tokenize` plutôt que par la sortie HTML de Prism
    // est ce qui ferme cette porte ; ce test le vérifie plutôt que de l'affirmer.
    const { container } = render(
      <SqlColore sql={"SELECT '<img src=x onerror=alert(1)>' AS a"} />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})
