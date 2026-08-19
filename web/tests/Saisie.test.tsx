import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Saisie } from '../src/composants/Saisie'

/**
 * Le texte en cours de frappe est détenu par ce composant, et non par `App` — c'est ce
 * qui empêche chaque caractère de re-rendre toute la conversation. Le contrat qui en
 * découle est vérifié ici, parce qu'il est le seul de ce lot à avoir changé : `App`
 * n'apprend le texte qu'à l'envoi, et le champ se vide de lui-même.
 */
describe('saisie', () => {
  it("rend le texte à l'envoi, puis vide le champ", () => {
    const envoye = vi.fn()
    render(<Saisie onEnvoyer={envoye} occupe={false} />)
    const champ = screen.getByPlaceholderText(/Poser une question/)

    fireEvent.change(champ, { target: { value: '  Quel budget média ?  ' } })
    fireEvent.click(screen.getByLabelText('Envoyer'))

    // Rogné : un envoi ne doit pas transporter les espaces de frappe.
    expect(envoye).toHaveBeenCalledWith('Quel budget média ?')
    expect((champ as HTMLTextAreaElement).value).toBe('')
  })

  it('envoie sur Entrée, passe à la ligne sur Maj+Entrée', () => {
    const envoye = vi.fn()
    render(<Saisie onEnvoyer={envoye} occupe={false} />)
    const champ = screen.getByPlaceholderText(/Poser une question/)

    fireEvent.change(champ, { target: { value: 'une question' } })
    fireEvent.keyDown(champ, { key: 'Enter', shiftKey: true })
    expect(envoye).not.toHaveBeenCalled()

    fireEvent.keyDown(champ, { key: 'Enter' })
    expect(envoye).toHaveBeenCalledWith('une question')
  })

  it("n'envoie rien pendant que l'agent travaille, ni sur un champ vide", () => {
    // Deux gardes distinctes : une question envoyée deux fois pendant que la première
    // tourne partirait en double appel facturé ; un champ vide n'a rien à envoyer.
    const envoye = vi.fn()
    const { rerender } = render(<Saisie onEnvoyer={envoye} occupe />)
    const champ = screen.getByPlaceholderText(/Poser une question/)

    expect(champ).toBeDisabled()
    fireEvent.keyDown(champ, { key: 'Enter' })
    expect(envoye).not.toHaveBeenCalled()

    rerender(<Saisie onEnvoyer={envoye} occupe={false} />)
    fireEvent.change(champ, { target: { value: '   ' } })
    fireEvent.keyDown(champ, { key: 'Enter' })
    expect(envoye).not.toHaveBeenCalled()
  })
})
