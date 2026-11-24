import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Empty, Page, Pill, Spinner } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

const label = (a: string) => a.replace('role_family:', '').replace('employer:', 'employer ').replace(/_/g, ' ')

export default function Preferences() {
  const { data, reload } = usePoll<any>(() => api.get('/preferences'), 0, () => false)
  if (!data) return <Page title="What I've learned"><Spinner label="Loading…" /></Page>
  const inferred = data.preferences.filter((p: any) => p.kind === 'inferred' && p.status !== 'superseded')
  const act = async (id: number, action: string, apply = false) => { await api.patch(`/preferences/${id}`, { action, apply_to_requirement: apply }); reload() }
  const req = data.requirements
  return (
    <Page title="What I've learned" kicker="Preferences & learning">
      <section className="card mb-5 p-5">
        <div className="flex items-baseline justify-between"><h2 className="text-xl">Your hard requirements</h2><Link to="/review" className="text-sm underline">Edit</Link></div>
        <p className="mt-1 text-xs text-ink/55">Only you can change these. I never relax them to find more matches.</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {req.min_salary && <Pill tone="forest">Min £{Number(req.min_salary.amount).toLocaleString()}</Pill>}
          {req.locations && <Pill tone="forest">{[...(req.locations.remote_regions || []).map((r: string) => `${r} remote`), ...(req.locations.hybrid_cities || []).map((c: string) => `${c} hybrid`)].join(' or ')}</Pill>}
          {req.max_office_days != null && <Pill tone="forest">≤ {req.max_office_days} office days</Pill>}
          {req.employment_types && <Pill tone="forest">{req.employment_types.join(', ').replace(/_/g, '-')}</Pill>}
          {req.deal_breakers?.on_call && <Pill tone="forest">No on-call</Pill>}
          {(req.deal_breakers?.keywords || []).map((k: string) => <Pill key={k} tone="forest">Not: {k}</Pill>)}
        </div>
      </section>

      <section className="mb-5">
        <h2 className="mb-1 text-xl">Hypotheses from your decisions</h2>
        <p className="mb-3 text-sm text-ink/60">I only act on patterns with repeated, consistent evidence. Confirm, correct or remove anything.</p>
        {inferred.length === 0 ? <Empty title="Nothing inferred yet">Swipe through a batch and I'll start noticing patterns.</Empty> : (
          <ul className="space-y-3">
            {inferred.map((p: any) => (
              <li key={p.id} className="card p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium capitalize text-forest">{label(p.attribute)} <span className="font-normal text-ink/60">→ {typeof p.value === 'object' ? JSON.stringify(p.value) : String(p.value).replace(/_/g, ' ')}</span></div>
                  <div className="flex gap-1.5">
                    <Pill tone={p.status === 'confirmed' ? 'forest' : p.status === 'rejected' ? 'clay' : 'gold'}>{p.status}</Pill>
                    {p.weight ? <Pill tone="plain">{p.weight > 0 ? '+' : ''}{p.weight} pts</Pill> : null}
                  </div>
                </div>
                <p className="mt-1.5 text-sm text-ink/75">{p.rationale}</p>
                <div className="mt-2 flex items-center gap-3 text-xs text-ink/55">
                  <span>Evidence: {p.supporting.length} supporting · {p.contradicting.length} contradicting</span>
                  <span aria-label={`Confidence ${Math.round(p.confidence * 100)}%`} className="inline-flex h-1.5 w-20 overflow-hidden rounded-full bg-sage"><span className="bg-fern" style={{ width: `${p.confidence * 100}%` }} /></span>
                </div>
                {p.question && p.status === 'proposed' && (
                  <div className="mt-3 rounded-2xl bg-gold/10 p-3 text-sm text-[#5e4b22]">
                    <b>{p.question}</b>
                    <p className="mt-1 text-xs">This would affect your hard requirement "{(p.affects_hard_requirement || '').replace(/_/g, ' ')}", so I'll only change it if you say yes.</p>
                    <div className="mt-2 flex gap-2"><button className="btn-primary py-1.5" onClick={() => act(p.id, 'confirm', true)}>Yes</button><button className="btn-ghost py-1.5" onClick={() => act(p.id, 'reject')}>No</button></div>
                  </div>
                )}
                {!p.question && p.status !== 'rejected' && (
                  <div className="mt-3 flex gap-2">
                    {p.status !== 'confirmed' && <button className="btn-soft py-1.5" onClick={() => act(p.id, 'confirm')}>That's right</button>}
                    <button className="btn-ghost py-1.5" onClick={() => act(p.id, 'reject')}>Not true</button>
                    <button className="btn-ghost py-1.5" onClick={() => act(p.id, 'remove')}>Remove</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl">How my search strategy has changed</h2>
        <ol className="space-y-3">
          {data.strategies.map((s: any) => (
            <li key={s.version} className="card p-4">
              <div className="flex items-center justify-between text-xs text-ink/55"><span>Version {s.version} · {s.created_by}</span><span>{new Date(s.created_at + 'Z').toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}</span></div>
              <div className="mt-2 flex flex-wrap gap-1.5">{s.titles.map((t: string) => <Pill key={t} tone="forest">{t}</Pill>)}{s.expanded_titles.map((t: string) => <Pill key={t} tone="gold">+ {t}</Pill>)}</div>
              {s.changes?.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-ink/75">{s.changes.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>}
            </li>
          ))}
        </ol>
      </section>
    </Page>
  )
}
