import type { Etape } from '../types'

/**
 * Ce que fait l'agent, pendant qu'il le fait.
 *
 * La raison d'être du flux : une question demande dix à trente secondes, et un indicateur
 * muet donne à croire que l'interface est figée. Montrer les étapes fait mieux que
 * rassurer — ça rend visible le travail réel : combien d'allers-retours, quelles requêtes,
 * combien de lignes ramenées.
 */
function libelle(etape: Etape): string {
  switch (etape.type) {
    case 'reflexion':
      return etape.tour === 1
        ? 'Lecture de la question et du schéma…'
        : `Analyse des résultats — étape ${etape.tour}/${etape.sur}…`
    case 'requete':
      return etape.erreur
        ? 'Requête refusée — nouvelle tentative…'
        : `Requête exécutée · ${etape.lignes} ligne${
            etape.lignes > 1 ? 's' : ''
          } · ${etape.duree_ms} ms`
    case 'redaction':
      return 'Rédaction de la réponse…'
  }
}

export function Progression({
  etapes,
  terminee = false,
}: {
  etapes: Etape[]
  /**
   * Une fois la réponse rendue, les étapes restent mais cessent de se signaler : plus de
   * pastille clignotante, plus de dernière ligne mise en avant. Elles deviennent une
   * trace qu'on relit, pas une activité qu'on suit — et sans ce basculement, une
   * conversation de cinq questions afficherait cinq points en train de clignoter pour
   * des exécutions terminées depuis longtemps.
   */
  terminee?: boolean
}) {
  return (
    <div className="flex flex-col gap-1.5 py-1">
      {etapes.map((etape, i) => {
        const derniere = !terminee && i === etapes.length - 1
        return (
          <div
            key={i}
            className={`flex items-center gap-2.5 text-sm ${
              derniere ? 'text-slate-300' : 'text-slate-600'
            }`}
          >
            <span
              className={`size-1.5 rounded-full shrink-0 ${
                etape.type === 'requete' && etape.erreur
                  ? 'bg-red-500'
                  : derniere
                    ? 'bg-sky-400 animate-pulse'
                    : 'bg-slate-700'
              }`}
            />
            <span className="font-mono text-xs">{libelle(etape)}</span>
          </div>
        )
      })}
    </div>
  )
}
