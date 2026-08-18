import Prism from 'prismjs'
import 'prismjs/components/prism-sql'
import type { ReactNode } from 'react'

/**
 * Coloration syntaxique du SQL.
 *
 * Prism sait produire directement une chaîne HTML, et ce serait deux lignes. On passe
 * plutôt par `tokenize`, qui rend un arbre de jetons, et on le transforme en éléments
 * React : **aucun HTML brut n'entre dans le DOM.** La règle vaut ici autant que pour le
 * Markdown des réponses — d'autant que le SQL affiché est écrit par le modèle, à partir
 * d'une question qui vient de l'utilisateur.
 *
 * Le thème est défini ici plutôt qu'importé d'une feuille Prism : quelques classes
 * suffisent, et une feuille externe imposerait ses propres couleurs de fond à un bloc
 * qui doit rester dans la palette de l'interface.
 */
const COULEURS: Record<string, string> = {
  keyword: 'text-violet-400 font-medium',
  function: 'text-sky-300',
  string: 'text-amber-300',
  number: 'text-emerald-300',
  operator: 'text-slate-400',
  punctuation: 'text-slate-500',
  comment: 'text-slate-600 italic',
  boolean: 'text-emerald-300',
  variable: 'text-slate-200',
}

function rendre(jeton: string | Prism.Token, cle: number): ReactNode {
  if (typeof jeton === 'string') return jeton

  const classe = COULEURS[jeton.type] ?? 'text-slate-300'
  const contenu = Array.isArray(jeton.content)
    ? jeton.content.map((j, i) => rendre(j, i))
    : rendre(jeton.content as string | Prism.Token, 0)

  return (
    <span key={cle} className={classe}>
      {contenu}
    </span>
  )
}

export function SqlColore({ sql }: { sql: string }) {
  const jetons = Prism.tokenize(sql, Prism.languages.sql)

  return (
    <pre
      className="px-3 py-2.5 text-xs font-mono leading-relaxed whitespace-pre
                 bg-slate-950/60 overflow-x-auto"
    >
      <code>{jetons.map((j, i) => rendre(j, i))}</code>
    </pre>
  )
}
