import { useState } from 'react'
import { salaryText } from '../api/client'
import { Pill } from './ui'

const WP: Record<string, string> = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'Office-based' }

export function ScoreRing({ score, uncertain }: { score: number; uncertain?: boolean }) {
  const r = 22, c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, score)) / 100
  return (
    <div className="relative h-14 w-14 shrink-0" title="Match score: how well this fits your stated preferences and experience. Not a probability of an offer.">
      <svg viewBox="0 0 56 56" className="h-14 w-14 -rotate-90">
        <circle cx="28" cy="28" r={r} fill="none" stroke="var(--color-sage)" strokeWidth="5" />
        <circle cx="28" cy="28" r={r} fill="none" stroke={uncertain ? 'var(--color-gold)' : 'var(--color-forest)'} strokeWidth="5"
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)} />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-sm font-semibold text-forest">{Math.round(score)}</div>
    </div>
  )
}

export function JobFacts({ job }: { job: any }) {
  const sal = salaryText(job.salary)
  return (
    <div className="flex flex-wrap gap-1.5">
      <Pill tone={sal ? 'forest' : 'gold'}>{sal ?? 'Salary not disclosed'}</Pill>
      {job.location && <Pill tone="plain">📍 {job.location.length > 48 ? job.location.slice(0, 46) + '…' : job.location}</Pill>}
      {job.workplace_type ? <Pill>{WP[job.workplace_type]}{job.workplace_type === 'remote' && job.remote_eligibility ? ` · ${job.remote_eligibility}` : ''}</Pill> : <Pill tone="gold">Work style not stated</Pill>}
      {job.workplace_type !== 'remote' && (job.office_days != null ? <Pill>{job.office_days} office day{job.office_days === 1 ? '' : 's'}/wk</Pill> : null)}
      {job.employment_type && <Pill tone="plain">{job.employment_type.replace('_', '-')}</Pill>}
      {job.on_call && <Pill tone="clay">On-call</Pill>}
      {job.is_synthetic && <Pill tone="gold">[DEMO] synthetic</Pill>}
    </div>
  )
}

export function MatchWhy({ job, compact = false }: { job: any; compact?: boolean }) {
  const m = job.match
  if (!m) return null
  return (
    <div className="space-y-2.5">
      <p className="text-[15px] leading-relaxed text-ink/85">{m.explanation}</p>
      {(m.concerns?.length > 0) && (
        <ul className="space-y-1">
          {m.concerns.slice(0, compact ? 2 : 6).map((c: string, i: number) => (
            <li key={i} className="flex gap-2 text-sm text-[#6f5a2e]"><span aria-hidden>◆</span><span>{c}</span></li>
          ))}
        </ul>
      )}
      {m.missing_info?.length > 0 && <p className="text-xs text-ink/50">Not stated by the employer: {m.missing_info.join(', ')}</p>}
    </div>
  )
}

export function Description({ job }: { job: any }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button className="btn-ghost -ml-3 px-3 py-1.5 text-sm" aria-expanded={open} onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
        {open ? 'Hide full description ▲' : 'Read full description ▼'}
      </button>
      {open && (
        job.description_html
          ? <div className="prose-job mt-2 max-h-80 overflow-y-auto rounded-2xl bg-mist/60 p-4 text-sm" dangerouslySetInnerHTML={{ __html: job.description_html }} />
          : <div className="prose-job mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-2xl bg-mist/60 p-4 text-sm">{job.description_text}</div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink/55">
        {job.source_url && <a className="font-medium text-forest underline" href={job.source_url} target="_blank" rel="noopener noreferrer">Original vacancy ↗</a>}
        <span>via {job.provider === 'demo' ? 'synthetic demo fixture' : job.provider === 'ashby' ? 'Ashby' : 'Greenhouse'}</span>
        {job.last_seen_at && <span>checked {new Date(job.last_seen_at + 'Z').toLocaleDateString('en-GB')}</span>}
        {job.lifecycle !== 'open' && <Pill tone="clay">{job.lifecycle.replace('_', ' ')}</Pill>}
      </div>
    </div>
  )
}
