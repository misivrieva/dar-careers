import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStatus } from '../App'
import { api } from '../api/client'
import { ErrorNote, Page, Pill, Spinner } from '../components/ui'

type Reqs = Record<string, any>

export default function ProfileReview() {
  const [p, setP] = useState<any>(null)
  const [reqs, setReqs] = useState<Reqs>({})
  const [titles, setTitles] = useState('')
  const [incl, setIncl] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const { refresh, status } = useStatus()
  const nav = useNavigate()

  useEffect(() => {
    api.get('/profile').then((d) => {
      setP(d); setReqs(d.requirements || {}); setIncl(d.user.include_uncertain)
      setTitles((d.preferences.target_titles || d.profile?.data?.suggested_target_titles || []).join(', '))
    })
  }, [])
  if (!p) return <Page title="Your profile"><Spinner label="Loading…" /></Page>
  const data = p.profile?.data || {}
  const loc = reqs.locations || { remote_regions: [], hybrid_cities: [], onsite_cities: [] }
  const setLoc = (k: string, v: string) => setReqs({ ...reqs, locations: { ...loc, [k]: v.split(',').map((x) => x.trim()).filter(Boolean) } })

  async function save(start: boolean) {
    setBusy(true); setErr(null)
    try {
      const keys = ['min_salary', 'locations', 'max_office_days', 'employment_types', 'needs_sponsorship', 'deal_breakers']
      const requirements = keys.map((k) => ({ key: k, value: reqs[k] ?? null }))
      await api.patch('/profile', { requirements, include_uncertain: incl, target_titles: titles.split(',').map((t) => t.trim()).filter(Boolean),
        complete_onboarding: start })
      await refresh()
      if (start) { await api.post('/discovery/start'); nav('/discover') }
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <Page title="Check what I understood" kicker={status?.onboarding_stage === 'done' ? 'Your profile' : 'Almost there'}>
      <p className="-mt-3 mb-6 text-ink/70">Correct anything that's off. <b>Hard requirements</b> are never relaxed without you; everything else just shapes ranking.</p>

      <section className="card mb-5 p-5">
        <h2 className="text-xl">Hard requirements</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="Minimum base salary (£)">
            <input type="number" className="inp" value={reqs.min_salary?.amount ?? ''} placeholder="Not set"
              onChange={(e) => setReqs({ ...reqs, min_salary: e.target.value ? { amount: Number(e.target.value), currency: 'GBP' } : null })} />
          </Field>
          <Field label="Max office days per week">
            <input type="number" min={0} max={5} className="inp" value={reqs.max_office_days ?? ''} placeholder="Not set"
              onChange={(e) => setReqs({ ...reqs, max_office_days: e.target.value === '' ? null : Number(e.target.value) })} />
          </Field>
          <Field label="Remote regions (comma separated)">
            <input className="inp" value={(loc.remote_regions || []).join(', ')} onChange={(e) => setLoc('remote_regions', e.target.value)} placeholder="e.g. UK" />
          </Field>
          <Field label="Hybrid cities">
            <input className="inp" value={(loc.hybrid_cities || []).join(', ')} onChange={(e) => setLoc('hybrid_cities', e.target.value)} placeholder="e.g. London" />
          </Field>
          <Field label="Employment types">
            <div className="flex flex-wrap gap-2">
              {['full_time', 'part_time', 'contract'].map((t) => {
                const on = (reqs.employment_types || []).includes(t)
                return <button key={t} type="button" aria-pressed={on} onClick={() => setReqs({ ...reqs, employment_types: on ? (reqs.employment_types || []).filter((x: string) => x !== t) : [...(reqs.employment_types || []), t] })}
                  className={`chip ${on ? 'border-forest bg-forest text-ivory' : 'border-sage bg-white text-forest'}`}>{on ? '✓ ' : ''}{t.replace('_', '-')}</button>
              })}
            </div>
          </Field>
          <Field label="Deal-breakers">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#304839]" checked={!!reqs.deal_breakers?.on_call}
              onChange={(e) => setReqs({ ...reqs, deal_breakers: { employers: [], keywords: [], ...(reqs.deal_breakers || {}), on_call: e.target.checked } })} /> No on-call, ever</label>
            <input className="inp mt-2" placeholder="Other keywords, comma separated" value={(reqs.deal_breakers?.keywords || []).join(', ')}
              onChange={(e) => setReqs({ ...reqs, deal_breakers: { employers: [], on_call: false, ...(reqs.deal_breakers || {}), keywords: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) } })} />
          </Field>
        </div>
        {p.unknowns?.length > 0 && <p className="mt-4 text-xs text-ink/55">Still unknown: {p.unknowns.join(', ')}. That's fine - I'll flag related uncertainty on job cards.</p>}
      </section>

      <section className="card mb-5 p-5">
        <h2 className="text-xl">What I'll search for</h2>
        <Field label="Target roles (comma separated)"><input className="inp" value={titles} onChange={(e) => setTitles(e.target.value)} /></Field>
        <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#304839]" checked={incl} onChange={(e) => setIncl(e.target.checked)} />
          Show roles with missing details (salary, office days), clearly flagged</label>
        <div className="mt-4 flex flex-wrap gap-1.5">
          {Object.entries(p.preferences || {}).filter(([k]) => !k.startsWith('unknown_') && k !== 'target_titles').map(([k, v]) => (
            <Pill key={k} tone="plain">{k.replace(/_/g, ' ')}: {typeof v === 'object' ? (v as any).amount ? `£${(v as any).amount.toLocaleString()}` : JSON.stringify(v).replace(/[{}"[\]]/g, ' ') : String(v)}</Pill>
          ))}
        </div>
      </section>

      <section className="card mb-6 p-5">
        <h2 className="text-xl">From your CV</h2>
        <p className="mt-1 text-xs text-ink/55">Extraction: {p.profile?.method === 'llm' ? 'AI, then every fact checked against your original CV' : 'rule-based (no AI)'}
          {data.extraction?.rejected?.length ? ` · ${data.extraction.rejected.length} item(s) dropped because they weren't in your CV` : ''}</p>
        <ul className="mt-4 space-y-3">
          {(data.roles || []).map((r: any) => (
            <li key={r.id} className="rounded-2xl bg-mist/70 p-3">
              <div className="font-medium text-forest">{r.title} · {r.employer}</div>
              <div className="text-xs text-ink/55">{r.start} – {r.end} · {r.bullets.length} verified points</div>
            </li>
          ))}
        </ul>
        {data.technical_skills?.length > 0 && <div className="mt-4 flex flex-wrap gap-1.5">{data.technical_skills.slice(0, 24).map((s: string) => <Pill key={s}>{s}</Pill>)}</div>}
        {(data.transferable_skills?.length > 0 || data.seniority) && (
          <div className="mt-4 rounded-2xl border border-gold/30 bg-gold/5 p-3 text-sm">
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-gold">AI interpretation (not a CV fact)</div>
            {data.seniority && data.seniority !== 'unknown' && <div>Seniority: {data.seniority}</div>}
            {data.transferable_skills?.length > 0 && <div>Transferable: {data.transferable_skills.join(', ')}</div>}
          </div>
        )}
      </section>
      {err && <div className="mb-4"><ErrorNote>{err}</ErrorNote></div>}
      <div className="flex flex-wrap gap-3">
        <button className="btn-primary px-7 py-3 text-base" disabled={busy} onClick={() => save(true)}>
          {status?.onboarding_stage === 'done' ? 'Save & find new matches' : 'Looks right - find my matches'}
        </button>
        <button className="btn-ghost" disabled={busy} onClick={() => save(false)}>Save for later</button>
      </div>
      <style>{`.inp{width:100%;border-radius:14px;border:1px solid var(--color-sage);background:#fff;padding:.6rem .9rem;font-size:15px;outline:none}.inp:focus{border-color:var(--color-gold)}`}</style>
    </Page>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-xs font-medium text-ink/60">{label}</span>{children}</label>
}
