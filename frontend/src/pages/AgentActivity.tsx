import { useState } from 'react'
import { api } from '../api/client'
import { Empty, Page, Pill, Spinner } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

export default function AgentActivity() {
  const { data } = usePoll<any>(() => api.get('/agent/runs'), 2500, (d) => !!d?.runs?.some((r: any) => r.status === 'running'))
  const [open, setOpen] = useState<number | null>(null)
  if (!data) return <Page title="Agent activity"><Spinner label="Loading…" /></Page>
  return (
    <Page title="Agent activity" kicker="Everything I've actually done">
      <p className="-mt-3 mb-5 text-sm text-ink/60">Observable actions and decisions only. Every tool call is validated in code; rejected calls are shown too.</p>
      {data.runs.length === 0 ? <Empty title="No runs yet" /> : (
        <ul className="space-y-3">
          {data.runs.map((r: any) => (
            <li key={r.id} className="card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium text-forest">{r.trigger.replace(/_/g, ' ')}</div>
                <div className="flex gap-1.5">
                  <Pill tone={r.status === 'completed' ? 'forest' : r.status === 'failed' ? 'clay' : 'gold'}>{r.status.replace('_', ' ')}</Pill>
                  <Pill tone="plain">{r.planner === 'llm' ? r.model : 'rule-based'}</Pill>
                </div>
              </div>
              {r.explanation && <p className="mt-2 text-sm text-ink/80">{r.explanation}</p>}
              <ol className="mt-3 space-y-1.5 border-l-2 border-sage pl-4">
                {(r.activity || []).map((a: any, i: number) => (
                  <li key={i} className={`text-sm ${a.kind === 'adaptation' ? 'font-medium text-[#5e4b22]' : a.kind === 'security' ? 'text-clay' : 'text-ink/70'}`}>{a.text}</li>
                ))}
              </ol>
              <button className="mt-3 text-xs text-forest underline" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? 'Hide' : 'Show'} execution trace ({r.tool_calls.length} tool calls · {r.llm_calls} model calls · {(r.input_tokens + r.output_tokens).toLocaleString()} tokens{r.estimated_cost_usd ? ` · ~$${r.estimated_cost_usd.toFixed(3)}` : ''})</button>
              {open === r.id && (
                <div className="mt-2 space-y-1.5 text-xs">
                  {r.strategy_summary && <div className="rounded-xl bg-mist p-2"><b>Strategy:</b> {r.strategy_summary}</div>}
                  {r.tool_calls.map((t: any, i: number) => (
                    <div key={i} className="rounded-xl bg-white p-2 ring-1 ring-sage">
                      <span className={`font-mono font-semibold ${t.status === 'ok' ? 'text-forest' : 'text-clay'}`}>{t.tool}</span>
                      <span className="ml-2 text-ink/45">{t.status} · {t.duration_ms} ms</span>
                      <div className="mt-0.5 break-all font-mono text-ink/55">{JSON.stringify(t.arguments)}</div>
                      {t.status !== 'ok' && <div className="mt-0.5 text-clay">{t.result}</div>}
                    </div>
                  ))}
                  {r.errors?.length > 0 && <div className="text-clay">Errors: {r.errors.join('; ')}</div>}
                  <div className="text-ink/45">Prompt version {r.prompt_version}</div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}
