import { useEffect, useRef, useState } from 'react'

/**
 * La zone de saisie.
 *
 * Entrée envoie, Maj+Entrée passe à la ligne — la convention des interfaces de discussion.
 * La hauteur suit le contenu jusqu'à un plafond : une question sur les données tient
 * souvent en deux lignes, et un champ d'une seule ligne oblige à relire à l'aveugle.
 *
 * **Le texte en cours de frappe est détenu ici, pas par `App`.** Il y vivait, et chaque
 * caractère re-rendait donc toute la conversation — graphiques Recharts compris. Le
 * garder local est ce qui rend la frappe indépendante de ce que la page affiche déjà.
 * `App` n'apprend le texte qu'à l'envoi, et le champ se vide lui-même à ce moment-là.
 */
export function Saisie({
  onEnvoyer,
  occupe,
}: {
  onEnvoyer: (texte: string) => void
  occupe: boolean
}) {
  const [valeur, setValeur] = useState('')
  const champ = useRef<HTMLTextAreaElement>(null)

  function envoyer() {
    const propre = valeur.trim()
    if (!propre || occupe) return
    // Vidé ici et non par l'appelant : c'est la contrepartie de détenir le texte. Vidé
    // *avant* l'appel, pour que le champ soit libre même si l'envoi échoue — une
    // question refusée par le réseau se repose, elle ne se retape pas.
    setValeur('')
    onEnvoyer(propre)
  }

  useEffect(() => {
    const e = champ.current
    if (!e) return
    e.style.height = 'auto'
    e.style.height = `${Math.min(e.scrollHeight, 200)}px`
  }, [valeur])

  useEffect(() => {
    if (!occupe) champ.current?.focus()
  }, [occupe])

  return (
    <div
      className="flex items-end gap-2 rounded-2xl border border-bordure-appuyee bg-surface
                 px-3 py-2 focus-within:border-accent transition-colors"
    >
      <textarea
        ref={champ}
        rows={1}
        value={valeur}
        disabled={occupe}
        onChange={(e) => setValeur(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            envoyer()
          }
        }}
        placeholder="Poser une question sur les données média…"
        className="flex-1 resize-none bg-transparent text-texte-fort
                   placeholder-texte-faible
                   outline-none text-[15px] leading-relaxed disabled:opacity-50 py-1"
      />
      <button
        onClick={envoyer}
        disabled={occupe || !valeur.trim()}
        aria-label="Envoyer"
        className="shrink-0 size-9 rounded-xl bg-accent-fond text-sur-accent
                   grid place-items-center
                   hover:bg-accent-fond-survol disabled:bg-surface-appuyee
                   disabled:text-texte-faible
                   transition-colors"
      >
        {occupe ? (
          <span className="size-3.5 rounded-full border-2 border-current border-t-transparent
                           animate-spin" />
        ) : (
          <span className="text-lg leading-none">↑</span>
        )}
      </button>
    </div>
  )
}
