import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, salaryText } from '../api/client'
import { Description, JobFacts, MatchWhy, ScoreRing } from '../components/JobCard'
import { APP_LABEL, Empty, ErrorNote, PREP_LABEL, Page, Pill, Spinner, prepTone } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

const TABS = [{ k: 'interested', d: 'yes', l: 'Interested' }, { k: 'maybe', d: 'maybe', l: 'Maybe' }, { k: 'passed', d: 'no', l: 'Passed' }]

export default function JobReview() {
  const [sp, setSp] = useSearchParams()
  const tab = TABS.find((t) => t.k === sp.get('tab')) ?? TABS[0]
  const [sort, setSort] = useState('relevance')
  const { data, reload } = usePoll<any>(() => api.get(`/jobs?decision=${tab.d}&sort=${sort}`), 3000,
    (d) => !!d?.jobs?.some((j: any) => ['pending', 'generating'].includes(j.application?.prep_status)))
  useEffect(() => { reload() }, [tab.k, sort]) // eslint-disable-line
  const [open, setOpen] = useState<number | null>(null)

  return (
    <Page title="Your jobs" kicker="Review at your own pace">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div role="tablist" className="flex rounded-full bg-sage/60 p-1">
          {TABS.map((t) => (
            <button key={t.k} role="tab" aria-selected={t.k === tab.k} onClick={() => { setSp({ tab: t.k }); setOpen(null) }}
              className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${t.k === tab.k ? 'bg-white text-forest shadow-sm' : 'text-forest/70'}`}>{t.l}</button>
          ))}
        </div>
        <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)} className="rounded-full border border-sage bg-white px-3 py-1.5 text-sm">
          <option value="relevance">Relevance</option><option value="salary">Salary</option><option value="date">Newest decision</option>
        </select>
      </div>
      {!data ? <Spinner label="Loading…" /> : data.jobs.length === 0 ? <Empty title="Nothing here yet">Jobs you mark in discovery appear here.</Empty> : (
        <ul className="space-y-3">
          {data.jobs.map((j: any) => (
            <li key={j.id} className="card overflow-hidden">
              <button className="flex w-full items-center gap-4 p-4 text-left" aria-expanded={open === j.id} onClick={() => setOpen(open === j.id ? null : j.id)}>
                {j.match && <ScoreRing score={j.match.score} uncertain={j.match.eligibility === 'uncertain'} />}
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium text-gold">{j.employer}</div>
                  <div className="truncate font-medium text-forest">{j.title}</div>
                  <div className="mt-0.5 text-xs text-ink/55">{salaryText(j.salary) ?? 'Salary not disclosed'} · {j.workplace_type ?? 'work style not stated'}{!j.active || j.lifecycle !== 'open' ? ' · may be closed' : ''}</div>
                </div>
                {j.application && <Pill tone={prepTone(j.application.prep_status)}>{PREP_LABEL[j.application.prep_status]}</Pill>}
              </button>
              <AnimatePresence>{open === j.id && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="border-t border-sage/60">
                  <JobDetail jobId={j.id} onChange={reload} />
                </motion.div>)}
              </AnimatePresence>
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}

function JobDetail({ jobId, onChange }: { jobId: number; onChange: () => void }) {
  const { data: job, reload } = usePoll<any>(() => api.get(`/jobs/${jobId}`), 2500,
    (d) => ['pending', 'generating'].includes(d?.application?.prep_status))
  const [comment, setComment] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (!job) return <div className="p-4"><Spinner /></div>
  const app = job.application
  async function change(decision: string) {
    try { await api.post(`/jobs/${jobId}/decision`, { decision }); await reload(); onChange() } catch (e: any) { setErr(e.message) }
  }
  async function saveComment() {
    await api.post(`/jobs/${jobId}/feedback`, { reasons: job.feedback?.reasons ?? [], comment })
    setComment(null); reload()
  }
  return (
    <div className="space-y-4 p-4">
      <JobFacts job={job} />
      <MatchWhy job={job} />
      <Description job={job} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink/55">Your decision:</span>
        {(['yes', 'maybe', 'no'] as const).map((d) => (
          <button key={d} onClick={() => change(d)} aria-pressed={job.decision === d}
            className={`chip ${job.decision === d ? 'border-forest bg-forest text-ivory' : 'border-sage bg-white text-forest'}`}>{d === 'yes' ? 'Interested' : d === 'maybe' ? 'Maybe' : 'Pass'}</button>
        ))}
      </div>
      <div>
        {comment === null ? (
          <button className="text-sm text-forest underline" onClick={() => setComment(job.feedback?.comment ?? '')}>{job.feedback?.comment ? `Note: “${job.feedback.comment}” (edit)` : 'Add a note'}</button>
        ) : (
          <div className="flex gap-2"><input autoFocus value={comment} onChange={(e) => setComment(e.target.value)} className="flex-1 rounded-full border border-sage px-4 py-2 text-sm" />
            <button className="btn-primary py-2" onClick={saveComment}>Save</button></div>
        )}
      </div>
      {app && <ApplicationPanel app={app} job={job} onChange={reload} />}
      {err && <ErrorNote>{err}</ErrorNote>}
    </div>
  )
}

function ApplicationPanel({ app, job, onChange }: { app: any; job: any; onChange: () => void }) {
  const [view, setView] = useState<'cv' | 'cover_letter' | null>(null)
  const v = app.validation || {}
  const cvDoc = app.documents.find((d: any) => d.kind === 'cv')
  const clDoc = app.documents.find((d: any) => d.kind === 'cover_letter')
  const issues = [...(v.deterministic?.issues || []), ...((v.judge?.issues || []).filter((i: any) => i.severity !== 'minor'))]
  const minor = (v.judge?.issues || []).filter((i: any) => i.severity === 'minor')
  return (
    <section className="rounded-3xl bg-mist/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg">Application package</h3>
        <Pill tone={prepTone(app.prep_status)}>{PREP_LABEL[app.prep_status]}</Pill>
      </div>
      {['pending', 'generating'].includes(app.prep_status) && <div className="mt-3"><Spinner label="Mapping the job's requirements to your verified experience, then fact-checking the drafts…" /></div>}
      {app.prep_status === 'failed' && <div className="mt-3"><ErrorNote>{app.error || 'Generation failed.'}</ErrorNote></div>}
      {app.documents.length > 0 && (<>
        <div className="mt-3 rounded-2xl bg-white p-3 text-sm">
          <div className="font-medium text-forest">Fact check: {v.status === 'passed' ? '✓ every claim traced to your CV' : v.status === 'issues_found' ? 'needs your attention' : 'please review manually'}</div>
          <div className="mt-1 text-xs text-ink/55">{app.generator}{v.judge?.model ? ` · checked independently by ${v.judge.model}` : ''}{v.note ? ` · ${v.note}` : ''}</div>
          {v.deterministic?.removed?.length > 0 && <div className="mt-2 text-xs text-ink/65">Automatically removed {v.deterministic.removed.length} unsupported line(s).</div>}
          {issues.length > 0 && <ul className="mt-2 space-y-1.5">{issues.map((i: any, k: number) => (
            <li key={k} className="rounded-xl bg-clay/5 p-2 text-xs text-clay"><b>{(i.problem || '').replace(/_/g, ' ')}</b> ({i.severity}): “{(i.claim || '').slice(0, 140)}” — {i.detail || i.explanation}</li>))}</ul>}
          {minor.length > 0 && <details className="mt-2 text-xs text-ink/60"><summary className="cursor-pointer">{minor.length} minor note(s)</summary>
            <ul className="mt-1 space-y-1">{minor.map((i: any, k: number) => <li key={k}>“{i.claim.slice(0, 120)}” — {i.explanation}</li>)}</ul></details>}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-soft py-2" onClick={() => setView(view === 'cv' ? null : 'cv')}>Preview CV</button>
          <button className="btn-soft py-2" onClick={() => setView(view === 'cover_letter' ? null : 'cover_letter')}>Preview cover letter</button>
          {cvDoc && <a className="btn-ghost py-2" href={`/api/documents/${cvDoc.id}/download`}>CV.docx</a>}
          {clDoc && <a className="btn-ghost py-2" href={`/api/documents/${clDoc.id}/download`}>Cover letter.docx</a>}
          <a className="btn-primary py-2" href={`/api/applications/${app.id}/download`}>Download package (.zip)</a>
        </div>
        {app.folder && <p className="mt-2 text-[11px] text-ink/45">Saved in storage/{app.folder} · version {app.version}</p>}
        {view === 'cv' && cvDoc && <CVPreview cv={cvDoc.preview} />}
        {view === 'cover_letter' && clDoc && <LetterPreview l={clDoc.preview} />}
        {app.requirement_map?.items?.length > 0 && (
          <details className="mt-3 text-sm"><summary className="cursor-pointer text-forest">How your experience maps to their requirements</summary>
            <ul className="mt-2 space-y-1">{app.requirement_map.items.map((r: any, i: number) => (
              <li key={i} className="flex gap-2"><span className={r.supported ? 'text-fern' : 'text-gold'}>{r.supported ? '✓' : '○'}</span><span>{r.requirement}{!r.supported && <span className="text-ink/50"> — not evidenced in your CV</span>}</span></li>))}</ul>
          </details>
        )}
      </>)}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-sage/70 pt-3">
        <label className="text-xs text-ink/55" htmlFor={`st-${app.id}`}>Application status</label>
        <select id={`st-${app.id}`} value={app.app_status} onChange={async (e) => { await api.patch(`/applications/${app.id}`, { app_status: e.target.value }); onChange() }}
          className="rounded-full border border-sage bg-white px-3 py-1.5 text-sm">
          {Object.entries(APP_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {['ready_for_review', 'needs_correction', 'failed', 'cancelled'].includes(app.prep_status) &&
          <button className="btn-ghost py-1.5 text-sm" onClick={async () => { await api.post(`/applications/${app.id}/regenerate`); onChange() }}>{app.prep_status === 'failed' ? 'Retry' : 'Regenerate'}</button>}
        {job.application_url && <a className="btn-ghost py-1.5 text-sm" href={job.application_url} target="_blank" rel="noopener noreferrer">Apply on employer site ↗</a>}
      </div>
      <p className="mt-2 text-[11px] text-ink/45">DAR never submits applications for you.</p>
    </section>
  )
}

function CVPreview({ cv }: { cv: any }) {
  return (
    <div className="mt-3 rounded-2xl bg-white p-5 text-sm leading-relaxed">
      <div className="font-display text-xl text-forest">{cv.name || 'Your name'}</div>
      <p className="mt-2">{cv.professional_summary}</p>
      {cv.key_skills?.length > 0 && <p className="mt-2 text-xs text-ink/70"><b>Key skills:</b> {cv.key_skills.join(', ')}</p>}
      {cv.roles.map((r: any) => (
        <div key={r.id} className="mt-3">
          <div className="font-medium text-forest">{r.title} — {r.employer} <span className="text-xs font-normal text-ink/50">{r.start} – {r.end}</span></div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{r.bullets.map((b: any, i: number) => <li key={i}>{b.text} <span className="text-[10px] text-ink/35">{b.evidence_ids.join(' ')}</span></li>)}</ul>
        </div>
      ))}
    </div>
  )
}

function LetterPreview({ l }: { l: any }) {
  return (
    <div className="mt-3 space-y-3 rounded-2xl bg-white p-5 text-sm leading-relaxed">
      <p>{l.greeting}</p>{l.paragraphs.map((p: any, i: number) => <p key={i}>{p.text}</p>)}<p>{l.closing}</p>
    </div>
  )
}
