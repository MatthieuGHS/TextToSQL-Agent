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
 *
 * Les neuf jetons `sql-*` sont **distincts** de ceux d'état, et c'est délibéré : une
 * chaîne SQL n'est pas un avertissement, un nombre n'est pas un succès. Les faire
 * partager un jeton les ferait diverger au premier ajustement de la palette d'état,
 * dans un sens que personne n'aurait voulu. Ils sont dérivés deux fois, comme les
 * douze couleurs de série — une palette calée sur un fond sombre ne se lit pas sur un
 * fond clair.
 */
const COULEURS: Record<string, string> = {
  keyword: 'text-sql-motcle font-medium',
  function: 'text-sql-fonction',
  string: 'text-sql-chaine',
  number: 'text-sql-nombre',
  operator: 'text-sql-operateur',
  punctuation: 'text-sql-ponctuation',
  comment: 'text-sql-commentaire italic',
  boolean: 'text-sql-nombre',
  variable: 'text-sql-variable',
}

function rendre(jeton: string | Prism.Token, cle: number): ReactNode {
  if (typeof jeton === 'string') return jeton

  const classe = COULEURS[jeton.type] ?? 'text-sql-defaut'
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
                 bg-surface-appuyee overflow-x-auto"
    >
      <code>{jetons.map((j, i) => rendre(j, i))}</code>
    </pre>
  )
}
