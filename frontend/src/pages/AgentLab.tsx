import { useState } from 'react'
import { api } from '../api/client'
import { Empty, Page, Pill, Spinner, Stat } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

export default function AgentLab() {
  const { data } = usePoll<any>(() => api.get('/evals/latest'), 0, () => false)
  const { data: emp } = usePoll<any>(() => api.get('/employers'), 0, () => false)
  const [mode, setMode] = useState<'live' | 'offline'>('live')
  if (!data) return <Page title="DAR Agent Lab"><Spinner label="Loading…" /></Page>
  if (!data.available) return <Page title="DAR Agent Lab" kicker="Internal evaluation"><Empty title="No evaluation runs yet">{data.message}</Empty></Page>
  const r = data.latest[mode] ?? data.latest[mode === 'live' ? 'offline' : 'live']
  const shownMode = data.latest[mode] ? mode : (mode === 'live' ? 'offline' : 'live')
  return (
    <Page title="DAR Agent Lab" kicker="Internal evaluation · genuine results only">
      <div className="mb-4 flex items-center gap-2">
        {(['live', 'offline'] as const).map((m) => (
          <button key={m} disabled={!data.latest[m]} onClick={() => setMode(m)}
            className={`chip ${shownMode === m ? 'border-forest bg-forest text-ivory' : 'border-sage bg-white text-forest'} disabled:opacity-40`}>
            {m === 'live' ? 'Live model evals' : 'Offline deterministic'}</button>
        ))}
      </div>
      <Run r={r} />
      {emp && (
        <section className="card mt-6 p-5">
          <h2 className="text-xl">Employer registry</h2>
          <p className="mt-1 text-sm text-ink/65">{emp.summary.verified} verified boards ({emp.summary.nasdaq100_supported} of {emp.summary.nasdaq100} Nasdaq-100 employers use a supported ATS) · {emp.summary.jobs_in_catalogue.toLocaleString()} live vacancies in the catalogue.</p>
          <details className="mt-3 text-sm"><summary className="cursor-pointer text-forest">Show boards</summary>
            <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto text-xs">
              {emp.employers.filter((e: any) => e.enabled).map((e: any) => (
                <li key={e.id} className="flex justify-between gap-2 border-b border-sage/50 py-1">
                  <span>{e.name} {e.nasdaq100 && <Pill tone="gold">N100</Pill>}</span>
                  <span className="text-ink/55">{e.provider} · {e.active_jobs} jobs ({e.uk_jobs} UK){e.last_error ? ` · ⚠ ${e.last_error}` : ''}</span>
                </li>))}
            </ul>
          </details>
        </section>
      )}
    </Page>
  )
}

function Run({ r }: { r: any }) {
  const [open, setOpen] = useState<string | null>(null)
  const t = r.totals
  return (
    <>
      <div className="card mb-5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-mono text-xs text-ink/55">{r.run_id}</div>
          <Pill tone={t.critical_failures ? 'clay' : t.failed ? 'gold' : 'forest'}>{t.critical_failures ? `${t.critical_failures} critical failure(s)` : t.failed ? `${t.failed} failed` : 'All executed cases passed'}</Pill>
        </div>
        <div className="mt-2 grid gap-1 text-xs text-ink/65 sm:grid-cols-2">
          <span>Generation model: <b>{r.model}</b></span><span>Judge model: <b>{r.judge_model}</b></span>
          <span>Dataset: {r.dataset_version}</span><span>Commit: {r.git_commit}</span>
          <span>Run at: {new Date(r.timestamp).toLocaleString('en-GB')}</span><span>Repetitions: {r.repetitions}</span>
        </div>
      </div>
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Executed" value={t.executed} /><Stat label="Passed" value={t.passed} /><Stat label="Failed" value={t.failed} />
        <Stat label="Skipped" value={t.skipped} /><Stat label="Uncertain" value={t.uncertain ?? 0} />
      </div>
      <section className="card mb-5 p-5">
        <h2 className="text-xl">Quality gates</h2>
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-xs text-ink/55"><tr><th className="py-1">Metric</th><th>Observed</th><th>n</th><th>Target</th><th /></tr></thead>
          <tbody>{r.quality_gates.map((g: any) => (
            <tr key={g.metric} className="border-t border-sage/60">
              <td className="py-1.5">{g.metric}</td><td>{g.observed ?? '—'}</td><td>{g.n}</td><td className="text-ink/55">{g.target}</td>
              <td>{g.met == null ? <Pill tone="plain">not measured</Pill> : g.met ? <Pill tone="forest">met</Pill> : <Pill tone="clay">not met</Pill>}</td>
            </tr>))}</tbody>
        </table>
        <p className="mt-3 text-[11px] text-ink/45">Targets are engineering goals, not claims of production reliability. Sample sizes are small; skipped cases never count as passes.</p>
      </section>
      <section className="card mb-5 p-5 text-sm">
        <h2 className="text-xl">Cost & latency</h2>
        <p className="mt-2 text-ink/70">{r.usage.llm_calls} model calls · {(r.usage.input_tokens + r.usage.output_tokens).toLocaleString()} tokens · avg {r.usage.latency_ms_avg ?? '—'} ms per call · {r.usage.cost_usd == null ? 'cost not configured' : `~$${r.usage.cost_usd.toFixed(4)}`}</p>
        {r.previous && <p className="mt-1 text-xs text-ink/55">Previous {r.mode} run {r.previous.run_id}: {r.previous.totals.passed}/{r.previous.totals.executed} passed.</p>}
      </section>
      <section>
        <h2 className="mb-3 text-xl">Cases</h2>
        <ul className="space-y-2">
          {r.cases.map((c: any) => (
            <li key={c.id} className="card overflow-hidden">
              <button className="flex w-full items-center justify-between gap-3 p-3 text-left" onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
                <div><div className="font-mono text-xs text-gold">{c.id} · {c.category}</div><div className="text-sm text-forest">{c.description}</div></div>
                <Pill tone={c.status === 'passed' ? 'forest' : c.status === 'failed' ? 'clay' : 'gold'}>{c.status}{c.severity === 'critical' ? ' · critical' : ''}</Pill>
              </button>
              {open === c.id && (
                <div className="space-y-2 border-t border-sage/60 p-3 text-xs">
                  <Block t="Expected" v={c.expected} /><Block t="Actual" v={c.actual} />
                  {c.evidence && <Block t="Evidence" v={c.evidence} />}
                  {c.failure && <div className="text-clay"><b>Failure:</b> {c.failure}</div>}
                  {c.repetitions > 1 && <div className="text-ink/55">Repetitions: {c.repetitions} · variability: {JSON.stringify(c.variability)}</div>}
                </div>
              )}
            </li>))}
        </ul>
      </section>
    </>
  )
}

function Block({ t, v }: { t: string; v: any }) {
  return <div><div className="font-medium text-forest">{t}</div><pre className="mt-0.5 max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-mist p-2 font-mono text-[11px]">{typeof v === 'string' ? v : JSON.stringify(v, null, 2)}</pre></div>
}
