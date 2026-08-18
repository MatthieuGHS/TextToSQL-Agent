import type { Cellule } from '../types'

/**
 * Les lignes brutes d'une requête.
 *
 * C'est ce qui rend une réponse vérifiable : le client voit la valeur, la requête qui l'a
 * produite, et le résultat de cette requête. Un chiffre sans sa provenance est exactement
 * ce que le projet cherche à éviter.
 *
 * L'affichage est borné à `MAX_LIGNES` — le serveur en renvoie jusqu'à 200, et dérouler
 * 200 lignes dans un fil de conversation le rend illisible. La troncature est **annoncée**,
 * comme celle du serveur : laisser croire qu'on a tout vu est le défaut à ne pas
 * reproduire ici.
 */
const MAX_LIGNES = 12

function formater(v: Cellule): string {
  if (v === null) return '∅'
  if (typeof v === 'number') return new Intl.NumberFormat('fr-FR').format(v)
  if (Array.isArray(v)) return v.map(formater).join(', ')
  return String(v)
}

export function TableauResultat({
  colonnes,
  lignes,
  tronque,
}: {
  colonnes: string[]
  lignes: Cellule[][]
  tronque: boolean
}) {
  if (lignes.length === 0) {
    return (
      <p className="px-3 py-2 text-sm text-slate-500 italic">
        Résultat vide : aucune ligne ne correspond.
      </p>
    )
  }

  const visibles = lignes.slice(0, MAX_LIGNES)
  const cachees = lignes.length - visibles.length

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              {colonnes.map((c) => (
                <th
                  key={c}
                  className="px-3 py-2 text-left font-medium text-slate-400
                             whitespace-nowrap"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibles.map((ligne, i) => (
              <tr key={i} className="border-b border-slate-800/60 last:border-0">
                {ligne.map((v, j) => (
                  <td
                    key={j}
                    className={`px-3 py-1.5 whitespace-nowrap ${
                      typeof v === 'number'
                        ? 'text-right font-mono tabular-nums text-slate-200'
                        : 'text-slate-300'
                    } ${v === null ? 'text-slate-600' : ''}`}
                  >
                    {formater(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(cachees > 0 || tronque) && (
        <p className="px-3 py-2 text-xs text-slate-500 border-t border-slate-800">
          {cachees > 0 && `${cachees} ligne${cachees > 1 ? 's' : ''} non affichée${
            cachees > 1 ? 's' : ''
          }`}
          {cachees > 0 && tronque && ' · '}
          {tronque && 'résultat tronqué par le plafond de lignes du serveur'}
        </p>
      )}
    </div>
  )
}
