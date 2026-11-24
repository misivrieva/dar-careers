import { motion } from 'framer-motion'
import type { ReactNode } from 'react'

export function Page({ title, kicker, children, actions }: { title: string; kicker?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <motion.main initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 30 }}
      className="mx-auto w-full max-w-3xl px-4 pb-28 pt-6 sm:px-6">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          {kicker && <p className="mb-1 text-xs font-medium uppercase tracking-[0.18em] text-gold">{kicker}</p>}
          <h1 className="text-3xl sm:text-4xl">{title}</h1>
        </div>
        {actions}
      </header>
      {children}
    </motion.main>
  )
}

export function Pill({ tone = 'sage', children, title }: { tone?: 'sage' | 'gold' | 'forest' | 'clay' | 'plain'; children: ReactNode; title?: string }) {
  const tones = {
    sage: 'bg-sage/70 text-forest border-sage',
    gold: 'bg-gold/15 text-[#6f5a2e] border-gold/40',
    forest: 'bg-forest text-ivory border-forest',
    clay: 'bg-clay/10 text-clay border-clay/30',
    plain: 'bg-white text-forest border-sage',
  }
  return <span title={title} className={`chip ${tones[tone]}`}>{children}</span>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-3 text-sm text-forest/80">
      <span className="relative inline-flex h-3 w-3">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-fern opacity-60" />
        <span className="relative inline-flex h-3 w-3 rounded-full bg-fern" />
      </span>
      {label}
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card p-8 text-center">
      <h3 className="text-xl">{title}</h3>
      {children && <div className="mt-2 text-sm text-ink/70">{children}</div>}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <div role="alert" className="rounded-2xl border border-clay/30 bg-clay/5 px-4 py-3 text-sm text-clay">{children}</div>
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card px-4 py-3">
      <div className="font-display text-2xl text-forest">{value}</div>
      <div className="text-xs text-ink/60">{label}</div>
      {hint && <div className="mt-1 text-[11px] text-ink/50">{hint}</div>}
    </div>
  )
}

export const PREP_LABEL: Record<string, string> = {
  pending: 'Queued', generating: 'Preparing…', ready_for_review: 'Ready for review', needs_correction: 'Needs your check',
  failed: 'Failed', cancelled: 'Cancelled',
}
export const APP_LABEL: Record<string, string> = {
  not_applied: 'Not applied', applied: 'Applied', interviewing: 'Interviewing', rejected: 'Rejected',
  offer_received: 'Offer received', withdrawn: 'Withdrawn',
}
export function prepTone(s?: string): 'sage' | 'gold' | 'forest' | 'clay' {
  return s === 'ready_for_review' ? 'forest' : s === 'needs_correction' || s === 'failed' ? 'clay' : s === 'cancelled' ? 'sage' : 'gold'
}
