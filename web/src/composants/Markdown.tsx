import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Le texte de l'agent, rendu.
 *
 * Mesuré sur les 171 exécutions du cache d'évaluation : 98 % des réponses contiennent du
 * Markdown — 97 % du code en ligne, 80 % du gras, 42 % des listes, 19 % un tableau. Ce
 * n'est donc pas un agrément, c'est le régime normal, et l'afficher brut revenait à
 * montrer la syntaxe au lieu du contenu.
 *
 * `remarkGfm` n'est pas facultatif : c'est lui qui apporte les tableaux.
 *
 * **`rehype-raw` n'est pas installé, et ne doit jamais l'être.** Par défaut React Markdown
 * n'interprète pas le HTML brut, et c'est la seule chose qui protège cette page : le texte
 * vient d'un modèle, qui lit lui-même une base de données. Une chaîne de caractères
 * malveillante stockée dans les données pourrait ressortir dans une réponse ; sans cette
 * garantie, elle s'exécuterait dans le navigateur. `tests/Markdown.test.tsx` en fait une
 * propriété vérifiée plutôt qu'une intention.
 */
export function Markdown({ texte }: { texte: string }) {
  return (
    <div
      className="prose max-w-none
                 prose-p:leading-relaxed prose-p:my-3
                 prose-headings:font-semibold prose-headings:text-texte-fort
                 prose-h1:text-lg prose-h2:text-base prose-h3:text-sm
                 prose-h2:mt-5 prose-h2:mb-2 prose-h3:mt-4 prose-h3:mb-1.5
                 prose-strong:text-texte-fort
                 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5
                 prose-hr:border-bordure
                 prose-a:text-accent prose-a:underline-offset-2"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Le code en ligne est le marqueur le plus fréquent (97 % des réponses) : ce
          // sont les noms de tables et de colonnes. La pastille les rend identifiables
          // sans avoir à lire la phrase.
          code: ({ children, ...props }) => (
            <code
              className="rounded bg-surface-appuyee px-1.5 py-0.5 text-[0.85em]
                         font-mono text-accent before:content-none after:content-none"
              {...props}
            >
              {children}
            </code>
          ),
          // Un tableau large casserait la mise en page de toute la conversation : il
          // défile dans son propre cadre.
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-bordure">
              <table className="my-0 w-full text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-bordure px-3 py-2 text-left font-medium
                           text-texte-attenue">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-bordure-attenuee px-3 py-1.5">{children}</td>
          ),
          // Un lien produit par le modèle est un lien qu'on n'a pas écrit : il s'ouvre
          // ailleurs, et sans donner la main sur l'onglet d'origine.
          a: ({ children, ...props }) => (
            <a target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {texte}
      </ReactMarkdown>
    </div>
  )
}
