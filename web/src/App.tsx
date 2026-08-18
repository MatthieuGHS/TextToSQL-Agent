import { useCallback, useEffect, useRef, useState } from 'react'
import { demander, sante as lireSante } from './api'
import { Entete } from './composants/Entete'
import { Message } from './composants/Message'
import { Progression } from './composants/Progression'
import { Saisie } from './composants/Saisie'
import type { Etape, Message as TypeMessage, Sante } from './types'

/**
 * Questions d'amorce.
 *
 * Tirées du corpus d'évaluation et **jamais de la grille client**, qui n'est pas
 * versionnée. Les trois montrent trois choses différentes : un piège du jeu de données
 * (`cost` existe dans deux tables à des périmètres différents), une analyse réelle sur
 * deux grains, et un refus expliqué là où les données ne permettent pas de conclure.
 */
const EXEMPLES = [
  'Quel est notre budget média total ?',
  'Comment les investissements TV et les mises en service ont-ils évolué semaine par ' +
    'semaine sur la dernière année de données ?',
  'Quel canal a le meilleur ROI ?',
]

/** Ce qu'on affiche quand la boucle n'a pas abouti normalement. */
const AVERTISSEMENTS: Record<string, string> = {
  reponse_tronquee:
    'Réponse incomplète : la limite de longueur a été atteinte. Reposer la question sur ' +
    'un périmètre plus étroit.',
  plafond_iterations:
    "L'agent n'a pas abouti dans le nombre d'étapes imparti. Les requêtes déjà exécutées " +
    'figurent ci-dessous.',
  trop_d_echecs_sql:
    "L'agent n'a pas réussi à écrire une requête valide après plusieurs tentatives.",
  refus_modele: "Le modèle a refusé de traiter cette question.",
  erreur_api: 'Le service de modèle est indisponible.',
}

export default function App() {
  const [messages, setMessages] = useState<TypeMessage[]>([])
  const [saisie, setSaisie] = useState('')
  const [etapes, setEtapes] = useState<Etape[]>([])
  const [occupe, setOccupe] = useState(false)
  const [sante, setSante] = useState<Sante | null>(null)
  const [erreurSante, setErreurSante] = useState<string | null>(null)
  const bas = useRef<HTMLDivElement>(null)
  const zone = useRef<HTMLElement>(null)

  useEffect(() => {
    lireSante().then(setSante).catch((e) => setErreurSante(String(e)))
  }, [])

  // Ne suit que si on est déjà en bas. Sans cette condition, remonter pour relire une
  // requête pendant que l'agent travaille ramène en bas à chaque étape — c'est-à-dire
  // toutes les quelques secondes, et précisément au moment d'une démonstration où on
  // remonte justement pour montrer quelque chose.
  const PRES_DU_BAS = 120

  useEffect(() => {
    const e = zone.current
    if (!e) return
    const distance = e.scrollHeight - e.scrollTop - e.clientHeight
    if (distance < PRES_DU_BAS) bas.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, etapes])

  const envoyer = useCallback(
    async (question: string) => {
      const propre = question.trim()
      if (!propre || occupe) return

      // Capturé avant la mise à jour d'état : `messages` est figé dans cette closure, et
      // c'est exactement ce qu'on veut envoyer — l'historique *avant* la nouvelle question.
      const precedents = messages
      setMessages([...precedents, { role: 'utilisateur', texte: propre }])
      setSaisie('')
      setEtapes([])
      setOccupe(true)

      try {
        const reponse = await demander(propre, precedents, (e) =>
          setEtapes((actuelles) => [...actuelles, e]),
        )
        setMessages((m) => [
          ...m,
          {
            role: 'agent',
            texte: reponse.texte,
            reponse,
            avertissement: reponse.arret_normal
              ? undefined
              : (AVERTISSEMENTS[reponse.arret] ?? "La réponse n'a pas abouti normalement."),
          },
        ])
      } catch (e) {
        setMessages((m) => [
          ...m,
          {
            role: 'agent',
            texte:
              "La question n'a pas pu être traitée. " +
              (e instanceof Error ? e.message : ''),
            avertissement: 'Erreur de communication avec le service.',
          },
        ])
      } finally {
        setOccupe(false)
        setEtapes([])
      }
    },
    [messages, occupe],
  )

  const vide = messages.length === 0

  return (
    <div className="h-full flex flex-col">
      <Entete sante={sante} erreur={erreurSante} />

      <main ref={zone} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-8 flex flex-col gap-8">
          {vide && (
            <div className="flex flex-col gap-4 pt-12">
              <div>
                <h2 className="text-lg font-medium text-slate-200">
                  Poser une question sur les données média
                </h2>
                <p className="text-sm text-slate-500 mt-1">
                  Chaque réponse s'accompagne des requêtes SQL exécutées et de leurs
                  résultats — pour que rien n'ait à être cru sur parole.
                </p>
              </div>
              <div className="flex flex-col gap-2">
                {EXEMPLES.map((e) => (
                  <button
                    key={e}
                    onClick={() => envoyer(e)}
                    className="text-left rounded-xl border border-slate-800 bg-slate-900/40
                               px-4 py-3 text-sm text-slate-300 hover:border-slate-600
                               hover:bg-slate-900 transition-colors"
                  >
                    {e}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <Message key={i} message={m} />
          ))}

          {occupe && <Progression etapes={etapes} />}
          <div ref={bas} />
        </div>
      </main>

      <footer className="shrink-0 border-t border-slate-800 bg-slate-950">
        <div className="mx-auto max-w-3xl px-6 py-4">
          <Saisie
            valeur={saisie}
            onChange={setSaisie}
            onEnvoyer={() => envoyer(saisie)}
            occupe={occupe}
          />
          <p className="mt-2 text-center text-xs text-slate-600">
            L'agent ne lit que la base fournie, en lecture seule. Il ne fait pas de
            modélisation d'attribution.
          </p>
        </div>
      </footer>
    </div>
  )
}
