import { motion } from 'framer-motion'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Page, Spinner, Stat } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

export default function Dashboard() {
  const { data } = usePoll<any>(() => api.get('/dashboard'), 4000, (d) => !!d && d.counts.applications_in_progress > 0)
  const nav = useNavigate()
  if (!data) return <Page title="Working for you"><Spinner label="Loading…" /></Page>
  const c = data.counts
  const na = data.next_action
  return (
    <Page title="Working for you" kicker="Your career agent">
      <motion.button whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }} onClick={() => nav(na.route)}
        className="card mb-6 flex w-full items-center justify-between gap-4 bg-forest p-5 text-left text-ivory">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-gold">Next step</div>
          <div className="mt-1 font-display text-xl sm:text-2xl">{na.label}</div>
        </div>
        <span aria-hidden className="text-2xl">→</span>
      </motion.button>

      {(data.questions?.length > 0 || data.agent_question) && (
        <div className="card mb-6 border-gold/40 bg-gold/5 p-4">
          <div className="text-xs font-medium uppercase tracking-wider text-gold">A quick question from your agent</div>
          {data.agent_question && <p className="mt-1 text-forest">{data.agent_question}</p>}
          {data.questions.map((q: any) => <p key={q.id} className="mt-1 text-forest">{q.question} <Link className="text-sm underline" to="/preferences">Answer</Link></p>)}
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="New opportunities" value={c.new_opportunities} />
        <Stat label="Interested" value={c.interested} />
        <Stat label="Worth revisiting" value={c.maybe} />
        <Stat label="Applications ready" value={c.applications_ready} hint={c.applications_in_progress ? `${c.applications_in_progress} being prepared` : undefined} />
      </div>
      {c.applications_in_progress > 0 && (
        <div className="card mb-6 overflow-hidden p-4">
          <div className="flex items-center justify-between text-sm"><span className="text-forest">Preparing {c.applications_in_progress} application{c.applications_in_progress > 1 ? 's' : ''}…</span></div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-sage/60"><motion.div className="h-full w-1/3 rounded-full bg-gold" animate={{ x: ['-100%', '300%'] }} transition={{ repeat: Infinity, duration: 1.6, ease: 'easeInOut' }} /></div>
        </div>
      )}

      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-xl">What I've been doing</h2>
          <Link to="/activity" className="text-sm text-forest underline">All activity</Link>
        </div>
        {data.activity.length === 0 ? <p className="text-sm text-ink/60">Nothing yet - start a discovery session and I'll get to work.</p> : (
          <ul className="space-y-3">
            {data.activity.slice(0, 3).map((r: any) => (
              <li key={r.id} className="card p-4">
                <div className="flex items-center justify-between text-xs text-ink/50">
                  <span>{r.trigger.replace(/_/g, ' ')} · {new Date(r.started_at + 'Z').toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}</span>
                  <span>{r.status.replace('_', ' ')}</span>
                </div>
                {r.explanation && <p className="mt-1.5 text-sm text-ink/80">{r.explanation}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="mt-8 text-center">
        <Link to="/review" className="text-sm text-ink/55 underline">Edit your profile &amp; requirements</Link>
      </div>
    </Page>
  )
}
