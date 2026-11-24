import { AnimatePresence, motion, useMotionValue, useTransform, type PanInfo } from 'framer-motion'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, uid } from '../api/client'
import { Description, JobFacts, MatchWhy, ScoreRing } from '../components/JobCard'
import { ErrorNote, Page, Pill, Spinner } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

type Decision = 'yes' | 'no' | 'maybe'
const REASONS: [string, string][] = [
  ['salary_too_low', 'Salary too low'], ['location_unsuitable', 'Location'], ['too_many_office_days', 'Too many office days'],
  ['requires_on_call', 'On-call'], ['too_much_coding', 'Too much coding'], ['wrong_seniority', 'Wrong seniority'],
  ['wrong_industry', 'Wrong industry'], ['not_interested_in_employer', 'Not this employer'], ['insufficient_progression', 'Little progression'],
  ['too_much_travel', 'Too much travel'], ['responsibilities_unsuitable', 'Not the right work'], ['other', 'Other'],
]
const YES_REASONS: [string, string][] = [['enjoy_customer_work', 'Customer-facing work'], ['good_tech_customer_mix', 'Tech + customer mix'],
  ['great_salary', 'Great salary'], ['great_flexibility', 'Flexibility']]

export default function Discover() {
  const nav = useNavigate()
  const { data, error, reload, setData } = usePoll<any>(() => api.get('/discovery/current'), 1500,
    (d) => !d?.session || d.session.status === 'preparing')
  const [toast, setToast] = useState<string | null>(null)
  const [fbFor, setFbFor] = useState<{ job: any; decision: Decision } | null>(null)
  const [, setHistory] = useState<number[]>([])
  const [busy, setBusy] = useState(false)

  const session = data?.session
  const jobs: any[] = data?.jobs ?? []
  const remaining: number[] = session?.remaining ?? []
  const queue = useMemo(() => jobs.filter((j) => remaining.includes(j.id)), [jobs, remaining])
  const current = queue[0]

  const start = useCallback(async (next = false) => {
    setBusy(true)
    try { setData(await api.post(next ? '/discovery/next' : '/discovery/start')); setHistory([]); poll() } finally { setBusy(false) }
  }, [setData])  // eslint-disable-line

  function poll() {
    const tick = async () => { const d = await reload(); if (d?.session?.status === 'preparing') setTimeout(tick, 1500) }
    setTimeout(tick, 1200)
  }

  useEffect(() => {
    if (data && !data.session) start(false)
  }, [data]) // eslint-disable-line

  const decide = useCallback(async (job: any, decision: Decision) => {
    if (!session) return
    setData((d: any) => ({ ...d, session: { ...d.session, remaining: d.session.remaining.filter((x: number) => x !== job.id),
      decided: { ...d.session.decided, [job.id]: decision } } }))
    setHistory((h) => [...h, job.id])
    if (decision === 'yes') flash(`Saved. I'm preparing a tailored CV and cover letter for ${job.employer}…`)
    if (decision === 'maybe') flash('Saved for later.')
    if (decision !== 'maybe') setFbFor({ job, decision })
    try {
      await api.post(`/jobs/${job.id}/swipe`, { decision, idempotency_key: uid(), session_id: session.id })
      reload()
    } catch (e: any) { flash(`Couldn't save that decision: ${e.message}`); reload() }
  }, [session, reload, setData])

  async function undo() {
    try {
      await api.post('/decisions/undo-last')
      setHistory((h) => h.slice(0, -1)); setFbFor(null); flash('Undone.'); reload()
    } catch (e: any) { flash(e.message) }
  }

  function flash(t: string) { setToast(t); window.setTimeout(() => setToast((x) => (x === t ? null : x)), 3200) }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT' || (e.target as HTMLElement)?.tagName === 'TEXTAREA') return
      if (e.key === 'u' || (e.key === 'z' && (e.metaKey || e.ctrlKey))) { e.preventDefault(); undo(); return }
      if (!current || fbFor) return
      if (e.key === 'ArrowRight') decide(current, 'yes')
      else if (e.key === 'ArrowLeft') decide(current, 'no')
      else if (e.key === 'ArrowDown') { e.preventDefault(); decide(current, 'maybe') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (error && !data) return <Page title="Discover"><ErrorNote>{error}</ErrorNote></Page>
  if (!data || !session) return <Page title="Discover"><Spinner label="Getting ready…" /></Page>

  const run = data.agent_run
  const adaptations = (run?.activity || []).filter((a: any) => a.kind === 'adaptation')

  if (session.status === 'preparing') return <Preparing run={run} />
  if (session.status === 'failed') return (
    <Page title="Discover"><ErrorNote>{session.note || 'The search failed.'}</ErrorNote>
      <button className="btn-primary mt-4" onClick={() => start(true)}>Try again</button></Page>)

  if (!current) return <Complete session={session} onMore={() => start(true)} onReview={() => nav('/jobs')} onFinish={async () => {
    if (session.status === 'active') await api.post(`/discovery/${session.id}/finish`); nav('/') }} busy={busy} empty={session.job_ids.length === 0} note={session.note} />

  const idx = session.job_ids.length - queue.length + 1
  return (
    <main className="mx-auto flex w-full max-w-xl flex-col px-4 pb-32 pt-4 sm:px-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex gap-1.5" aria-label={`Job ${idx} of ${session.job_ids.length}`}>
          {session.job_ids.map((id: number) => (
            <span key={id} className={`h-1.5 w-6 rounded-full transition ${session.decided[id] ? (session.decided[id] === 'yes' ? 'bg-forest' : session.decided[id] === 'maybe' ? 'bg-gold' : 'bg-ink/25') : id === current.id ? 'bg-fern' : 'bg-sage'}`} />
          ))}
        </div>
        <span className="text-xs text-ink/55">{idx} of {session.job_ids.length}</span>
      </div>
      {adaptations.length > 0 && idx === 1 && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} className="mb-3 rounded-2xl border border-gold/40 bg-gold/10 px-4 py-2.5 text-sm text-[#5e4b22]">
          <b>I adapted to your feedback:</b> {adaptations[adaptations.length - 1].text.replace('Adapted search strategy: ', '')}
        </motion.div>
      )}
      <div className="relative min-h-[calc(100dvh-250px)]">
        <AnimatePresence>
          {queue.slice(0, 2).reverse().map((job) => (
            <SwipeCard key={job.id} job={job} top={job.id === current.id} onDecide={(d) => decide(job, d)} />
          ))}
        </AnimatePresence>
      </div>
      <div className="fixed inset-x-0 bottom-[68px] z-20 pb-[env(safe-area-inset-bottom)]">
        <div className="mx-auto flex max-w-xl items-center justify-center gap-4 px-4 py-3">
          <RoundBtn label="Undo (U)" onClick={undo} disabled={!Object.keys(session.decided).length} small>↶</RoundBtn>
          <RoundBtn label="No (←)" onClick={() => decide(current, 'no')} tone="no">✕</RoundBtn>
          <RoundBtn label="Maybe (↓)" onClick={() => decide(current, 'maybe')} tone="maybe">↓</RoundBtn>
          <RoundBtn label="Yes (→)" onClick={() => decide(current, 'yes')} tone="yes">✓</RoundBtn>
          <button className="btn-ghost h-11 px-3 text-xs" onClick={() => nav('/')}>Exit</button>
        </div>
      </div>
      <AnimatePresence>{fbFor && <FeedbackSheet key={fbFor.job.id} {...fbFor} onClose={() => setFbFor(null)} />}</AnimatePresence>
      <AnimatePresence>{toast && (
        <motion.div initial={{ opacity: 0, y: -16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -16 }} role="status"
          className="fixed inset-x-0 top-16 z-40 mx-auto w-fit max-w-[90%] rounded-full bg-forest px-5 py-2.5 text-sm text-ivory shadow-lg">{toast}</motion.div>)}
      </AnimatePresence>
    </main>
  )
}

function RoundBtn({ children, label, onClick, tone, disabled, small }: any) {
  const tones: Record<string, string> = { no: 'bg-white text-clay border-clay/30', maybe: 'bg-white text-gold border-gold/40', yes: 'bg-forest text-ivory border-forest' }
  return (
    <motion.button whileTap={{ scale: 0.88 }} whileHover={{ y: -2 }} aria-label={label} title={label} onClick={onClick} disabled={disabled}
      className={`grid place-items-center rounded-full border text-xl shadow-[var(--shadow-soft)] disabled:opacity-40 ${small ? 'h-11 w-11 bg-white text-base text-forest/70 border-sage' : 'h-14 w-14'} ${tone ? tones[tone] : ''}`}>
      {children}
    </motion.button>
  )
}

function SwipeCard({ job, top, onDecide }: { job: any; top: boolean; onDecide: (d: Decision) => void }) {
  const x = useMotionValue(0), y = useMotionValue(0)
  const rotate = useTransform(x, [-240, 240], [-10, 10])
  const yesO = useTransform(x, [30, 130], [0, 1]), noO = useTransform(x, [-130, -30], [1, 0]), maybeO = useTransform(y, [30, 130], [0, 1])
  const [exit, setExit] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const dragged = useRef(false)

  function onEnd(_: unknown, info: PanInfo) {
    const { offset, velocity } = info
    if (offset.x > 120 || velocity.x > 700) { setExit({ x: 600, y: offset.y }); onDecide('yes') }
    else if (offset.x < -120 || velocity.x < -700) { setExit({ x: -600, y: offset.y }); onDecide('no') }
    else if (offset.y > 120 || velocity.y > 700) { setExit({ x: 0, y: 700 }); onDecide('maybe') }
    setTimeout(() => (dragged.current = false), 50)
  }
  const m = job.match || {}
  return (
    <motion.article layout drag={top} dragSnapToOrigin onDragStart={() => (dragged.current = true)} onDragEnd={onEnd}
      style={{ x, y, rotate, zIndex: top ? 2 : 1 }} initial={{ scale: 0.95, opacity: 0, y: 14 }}
      animate={{ scale: top ? 1 : 0.95, opacity: 1, y: top ? 0 : 14 }}
      exit={{ x: exit.x || (x.get() > 0 ? 600 : x.get() < 0 ? -600 : 0), y: exit.y || (Math.abs(x.get()) < 20 ? 700 : 0), opacity: 0, transition: { duration: 0.32 } }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
      className={`card absolute inset-x-0 top-0 flex max-h-[calc(100dvh-250px)] min-h-[420px] cursor-grab touch-pan-y select-none flex-col !bg-white p-5 active:cursor-grabbing ${top ? '' : 'pointer-events-none'}`}
      aria-label={`${job.title} at ${job.employer}`}>
      <motion.div style={{ opacity: yesO }} className="pointer-events-none absolute right-5 top-5 rotate-6 rounded-xl border-2 border-forest px-3 py-1 font-display text-lg text-forest">Yes</motion.div>
      <motion.div style={{ opacity: noO }} className="pointer-events-none absolute left-5 top-5 -rotate-6 rounded-xl border-2 border-clay px-3 py-1 font-display text-lg text-clay">No</motion.div>
      <motion.div style={{ opacity: maybeO }} className="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2 rounded-xl border-2 border-gold px-3 py-1 font-display text-lg text-gold">Maybe</motion.div>
      <div className="flex shrink-0 items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gold">{job.employer}</p>
          <h2 className="mt-0.5 text-2xl leading-snug">{job.title}</h2>
        </div>
        {m.score != null && <ScoreRing score={m.score} uncertain={m.eligibility === 'uncertain'} />}
      </div>
      <div className="-mx-1 mt-1 min-h-0 flex-1 overflow-y-auto px-1">
        <div className="mt-3"><JobFacts job={job} /></div>
        {m.eligibility === 'uncertain' && <div className="mt-3"><Pill tone="gold">Some requirements can't be confirmed - see below</Pill></div>}
        <div className="mt-4"><MatchWhy job={job} compact /></div>
        <div className="mt-4 border-t border-sage/60 pt-3" onPointerDownCapture={(e) => e.stopPropagation()}><Description job={job} /></div>
      </div>
    </motion.article>
  )
}

function FeedbackSheet({ job, decision, onClose }: { job: any; decision: Decision; onClose: () => void }) {
  const [sel, setSel] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const opts = decision === 'yes' ? YES_REASONS : REASONS
  async function send() {
    if (sel.length || comment.trim()) await api.post(`/jobs/${job.id}/feedback`, { reasons: sel, comment: comment.trim() || null })
    onClose()
  }
  useEffect(() => { const t = window.setTimeout(() => { if (!sel.length && !comment) onClose() }, 9000); return () => window.clearTimeout(t) }, [sel, comment, onClose])
  return (
    <motion.div initial={{ y: 200, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 200, opacity: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 28 }}
      className="fixed inset-x-0 bottom-[140px] z-30 mx-auto w-[min(92%,34rem)] rounded-3xl border border-sage bg-white p-4 shadow-[var(--shadow-lift)]" role="dialog" aria-label="Optional feedback">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-forest">{decision === 'yes' ? 'What appealed? (optional)' : 'Why not? (optional)'}</p>
        <button className="text-xs text-ink/50 hover:text-forest" onClick={onClose}>Skip</button>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {opts.map(([k, l]) => {
          const on = sel.includes(k)
          return <button key={k} aria-pressed={on} onClick={() => setSel(on ? sel.filter((x) => x !== k) : [...sel, k])}
            className={`chip ${on ? 'border-forest bg-forest text-ivory' : 'border-sage bg-white text-forest hover:border-gold'}`}>{l}</button>
        })}
      </div>
      <div className="mt-2.5 flex gap-2">
        <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Anything else?" className="flex-1 rounded-full border border-sage px-4 py-2 text-sm outline-none focus:border-gold" />
        <button className="btn-primary py-2" onClick={send}>Save</button>
      </div>
    </motion.div>
  )
}

function Preparing({ run }: { run: any }) {
  const acts = run?.activity || []
  return (
    <Page title="Working for you" kicker="Your agent is searching">
      <div className="card p-6">
        <Spinner label={acts.length ? acts[acts.length - 1].text : 'Planning your search…'} />
        <ol className="mt-5 space-y-2.5">
          <AnimatePresence initial={false}>
            {acts.slice(0, -1).map((a: any, i: number) => (
              <motion.li key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} className="flex gap-3 text-sm text-ink/75">
                <span className="mt-0.5 text-fern">✓</span><span>{a.text}</span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
        <p className="mt-5 text-xs text-ink/45">Everything listed here actually happened - nothing is simulated. {run?.planner === 'rules' ? 'Rule-based planner (no AI key).' : ''}</p>
      </div>
    </Page>
  )
}

function Complete({ session, onMore, onReview, onFinish, busy, empty, note }: any) {
  const s = session.summary
  return (
    <Page title={empty ? 'Nothing new right now' : 'Batch complete'} kicker={empty ? 'Honest results' : 'Nice work'}>
      {empty ? <p className="-mt-2 mb-5 text-ink/70">{note || "I didn't find new roles that meet your requirements. I won't pad the list with poor matches."}</p> : (
        <motion.div initial={{ scale: 0.96, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 200, damping: 20 }}
          className="card mb-6 p-6">
          <p className="font-display text-2xl text-forest">You reviewed {s.reviewed} opportunities.</p>
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            <div className="rounded-2xl bg-forest/90 p-3 text-ivory"><div className="font-display text-2xl">{s.interested}</div><div className="text-xs">Interested</div></div>
            <div className="rounded-2xl bg-gold/20 p-3 text-[#5e4b22]"><div className="font-display text-2xl">{s.maybe}</div><div className="text-xs">Maybe</div></div>
            <div className="rounded-2xl bg-sage/70 p-3 text-forest"><div className="font-display text-2xl">{s.rejected}</div><div className="text-xs">Passed</div></div>
          </div>
          <p className="mt-4 text-sm text-ink/70">
            {s.applications_prepared} application{s.applications_prepared === 1 ? '' : 's'} prepared
            {s.applications_generating ? ` · ${s.applications_generating} still being written` : ''}. Every decision is saved - stopping here is completely fine.
          </p>
        </motion.div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <button className="btn-primary py-3" onClick={onMore} disabled={busy}>Discover more</button>
        <button className="btn-soft py-3" onClick={onReview}>Review selected jobs</button>
        <button className="btn-ghost py-3" onClick={onFinish}>Finish for now</button>
      </div>
    </Page>
  )
}
