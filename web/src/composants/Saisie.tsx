import { useEffect, useRef } from 'react'

/**
 * La zone de saisie.
 *
 * Entrée envoie, Maj+Entrée passe à la ligne — la convention des interfaces de discussion.
 * La hauteur suit le contenu jusqu'à un plafond : une question sur les données tient
 * souvent en deux lignes, et un champ d'une seule ligne oblige à relire à l'aveugle.
 */
export function Saisie({
  valeur,
  onChange,
  onEnvoyer,
  occupe,
}: {
  valeur: string
  onChange: (v: string) => void
  onEnvoyer: () => void
  occupe: boolean
}) {
  const champ = useRef<HTMLTextAreaElement>(null)

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
      className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-slate-900
                 px-3 py-2 focus-within:border-slate-500 transition-colors"
    >
      <textarea
        ref={champ}
        rows={1}
        value={valeur}
        disabled={occupe}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onEnvoyer()
          }
        }}
        placeholder="Poser une question sur les données média…"
        className="flex-1 resize-none bg-transparent text-slate-100 placeholder-slate-600
                   outline-none text-[15px] leading-relaxed disabled:opacity-50 py-1"
      />
      <button
        onClick={onEnvoyer}
        disabled={occupe || !valeur.trim()}
        aria-label="Envoyer"
        className="shrink-0 size-9 rounded-xl bg-sky-600 text-white grid place-items-center
                   hover:bg-sky-500 disabled:bg-slate-800 disabled:text-slate-600
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
