import { useCallback, useEffect, useRef, useState } from 'react'
import { etatDonnees, recharger } from '../api'
import type { EtatDonnees, EvenementEtl, FichierSource } from '../types'

/**
 * Chargement des sources et relance de la pipeline.
 *
 * Le geste est plus lourd qu'il n'en a l'air : la pipeline **reconstruit tout** depuis
 * `data/raw/`, elle ne complète pas. L'interface le dit plutôt que de le laisser
 * découvrir — un utilisateur qui croit ajouter des semaines et qui remplace l'historique
 * ferait une erreur coûteuse et silencieuse.
 *
 * Le journal affiché est celui de l'ETL, tel quel. Il porte la volumétrie par table et le
 * résultat des invariants : c'est exactement ce qu'il faut lire pour savoir si un
 * chargement s'est bien passé, et c'est déjà écrit.
 */
function taille(octets: number | null): string {
  if (octets === null) return '—'
  const mo = octets / (1024 * 1024)
  return mo >= 1 ? `${mo.toFixed(1)} Mo` : `${Math.max(1, Math.round(octets / 1024))} ko`
}

function date(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('fr-FR') + ' ' + d.toLocaleTimeString('fr-FR', {
    hour: '2-digit', minute: '2-digit',
  })
}

function LigneFichier({
  fichier,
  choisi,
}: {
  fichier: FichierSource
  choisi: File | undefined
}) {
  return (
    <tr className="border-b border-slate-800/60 last:border-0">
      <td className="py-2 pr-3">
        <code className="font-mono text-xs text-slate-300">{fichier.nom}</code>
        {!fichier.requis && (
          <span className="ml-2 text-xs text-slate-600">
            facultatif — sert au contrôle de couverture
          </span>
        )}
      </td>
      <td className="py-2 pr-3 text-xs text-slate-500 tabular-nums whitespace-nowrap">
        {taille(fichier.octets)}
      </td>
      <td className="py-2 pr-3 text-xs text-slate-500 tabular-nums whitespace-nowrap">
        {date(fichier.modifie_le)}
      </td>
      <td className="py-2 text-xs whitespace-nowrap">
        {choisi ? (
          <span className="text-sky-400">↑ remplacé par le fichier choisi</span>
        ) : fichier.present ? (
          <span className="text-emerald-500">en place</span>
        ) : (
          <span className={fichier.requis ? 'text-red-400' : 'text-slate-600'}>
            {fichier.requis ? 'manquant' : 'absent'}
          </span>
        )}
      </td>
    </tr>
  )
}

export function PageDonnees() {
  const [etat, setEtat] = useState<EtatDonnees | null>(null)
  const [choisis, setChoisis] = useState<File[]>([])
  const [journal, setJournal] = useState<EvenementEtl[]>([])
  const [occupe, setOccupe] = useState(false)
  const champ = useRef<HTMLInputElement>(null)
  const bas = useRef<HTMLDivElement>(null)

  const rafraichir = useCallback(() => {
    etatDonnees().then(setEtat).catch(() => setEtat(null))
  }, [])

  useEffect(rafraichir, [rafraichir])
  useEffect(() => bas.current?.scrollIntoView({ behavior: 'smooth' }), [journal])

  const lancer = useCallback(async () => {
    setJournal([])
    setOccupe(true)
    try {
      await recharger(choisis, (e) => setJournal((j) => [...j, e]))
      setChoisis([])
      if (champ.current) champ.current.value = ''
    } catch (e) {
      setJournal((j) => [
        ...j,
        { type: 'erreur', message: e instanceof Error ? e.message : String(e) },
      ])
    } finally {
      setOccupe(false)
      rafraichir()
    }
  }, [choisis, rafraichir])

  const parNom = new Map(choisis.map((f) => [f.name, f]))
  const termine = journal.some((e) => e.type === 'termine')
  const echoue = journal.some((e) => e.type === 'erreur')

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-medium text-slate-200">Données sources</h2>
        <p className="mt-1 text-sm text-slate-500">
          Charger de nouveaux fichiers relance la pipeline complète : transformation,
          contrôle du contrat de données, reconstruction de la base.{' '}
          <strong className="text-slate-400">
            La base est reconstruite entièrement, pas complétée
          </strong>{' '}
          — un fichier envoyé remplace sa version précédente.
        </p>
      </div>

      {etat && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-xs uppercase tracking-wide
                             text-slate-600">
                <th className="pb-2 text-left font-medium">Fichier</th>
                <th className="pb-2 text-left font-medium">Taille</th>
                <th className="pb-2 text-left font-medium">Modifié</th>
                <th className="pb-2 text-left font-medium">État</th>
              </tr>
            </thead>
            <tbody>
              {etat.fichiers.map((f) => (
                <LigneFichier key={f.nom} fichier={f} choisi={parNom.get(f.nom)} />
              ))}
            </tbody>
          </table>
          <p className="mt-3 border-t border-slate-800 pt-3 text-xs text-slate-600">
            Base : {etat.base_presente
              ? `${etat.tables.length} tables · reconstruite le ${date(etat.base_modifiee_le)}`
              : 'absente — un premier chargement la créera'}
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={champ}
          type="file"
          multiple
          accept=".csv"
          disabled={occupe}
          onChange={(e) => setChoisis([...(e.target.files ?? [])])}
          className="text-sm text-slate-400 file:mr-3 file:rounded-lg file:border-0
                     file:bg-slate-800 file:px-3 file:py-2 file:text-sm file:text-slate-200
                     hover:file:bg-slate-700 file:cursor-pointer disabled:opacity-50"
        />
        <button
          onClick={lancer}
          disabled={occupe}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white
                     hover:bg-sky-500 disabled:bg-slate-800 disabled:text-slate-600
                     transition-colors"
        >
          {occupe
            ? 'Reconstruction…'
            : choisis.length > 0
              ? `Charger ${choisis.length} fichier${choisis.length > 1 ? 's' : ''} et reconstruire`
              : 'Reconstruire depuis les sources en place'}
        </button>
      </div>

      {journal.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 overflow-hidden">
          <div className="max-h-96 overflow-y-auto px-4 py-3 font-mono text-xs">
            {journal.map((e, i) => (
              <div
                key={i}
                className={
                  e.type === 'erreur'
                    ? 'whitespace-pre-wrap py-0.5 text-red-400'
                    : e.type === 'termine'
                      ? 'py-0.5 text-emerald-400'
                      : e.niveau === 'warning'
                        ? 'py-0.5 text-amber-400'
                        : e.niveau === 'error'
                          ? 'py-0.5 text-red-400'
                          : 'py-0.5 text-slate-500'
                }
              >
                {e.type === 'termine'
                  ? `✓ Base reconstruite${
                      e.fichiers.length ? ` · ${e.fichiers.join(', ')}` : ''
                    }`
                  : e.message}
              </div>
            ))}
            <div ref={bas} />
          </div>
          {(termine || echoue) && (
            <p className="border-t border-slate-800 px-4 py-2 text-xs text-slate-500">
              {termine
                ? "L'agent a été réinitialisé : il décrit désormais les nouvelles données."
                : 'Rien n’a été remplacé — les sources et la base précédentes sont intactes.'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
