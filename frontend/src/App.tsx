import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api/client'
import { Spinner } from './components/ui'
import AgentActivity from './pages/AgentActivity'
import AgentLab from './pages/AgentLab'
import Dashboard from './pages/Dashboard'
import Discover from './pages/Discover'
import JobReview from './pages/JobReview'
import Onboarding from './pages/Onboarding'
import Preferences from './pages/Preferences'
import ProfileReview from './pages/ProfileReview'

type Status = { llm_available: boolean; llm_provider: string; generation_model: string; eval_model: string; demo_mode: boolean; onboarding_stage: string; llm_consent: boolean }
const StatusCtx = createContext<{ status: Status | null; refresh: () => Promise<void> }>({ status: null, refresh: async () => {} })
export const useStatus = () => useContext(StatusCtx)

const NAV = [
  { to: '/', label: 'Home', icon: '◐' },
  { to: '/discover', label: 'Discover', icon: '✦' },
  { to: '/jobs', label: 'Jobs', icon: '❒' },
  { to: '/preferences', label: 'Learning', icon: '◇' },
  { to: '/activity', label: 'Agent', icon: '≋' },
]

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const refresh = useCallback(async () => setStatus(await api.get('/status')), [])
  useEffect(() => { refresh() }, [refresh])
  const loc = useLocation()
  if (!status) return <div className="grid h-full place-items-center"><Spinner label="Opening DAR…" /></div>
  const stage = status.onboarding_stage
  const onboardingRoute = stage === 'review' ? '/review' : '/onboarding'
  const gated = stage !== 'done' && !['/onboarding', '/review', '/lab'].includes(loc.pathname)
  return (
    <StatusCtx.Provider value={{ status, refresh }}>
      <div className="min-h-full">
        <TopBar status={status} />
        {status.demo_mode && (
          <div className="bg-gold/20 px-4 py-1.5 text-center text-xs font-medium text-[#5e4b22]">
            Demo mode — vacancies marked [DEMO] are synthetic fixtures, not live jobs.
          </div>
        )}
        {gated ? <Navigate to={onboardingRoute} replace /> : (
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/review" element={<ProfileReview />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/jobs" element={<JobReview />} />
            <Route path="/preferences" element={<Preferences />} />
            <Route path="/activity" element={<AgentActivity />} />
            <Route path="/lab" element={<AgentLab />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
        {stage === 'done' && <BottomNav />}
      </div>
    </StatusCtx.Provider>
  )
}

function TopBar({ status }: { status: Status }) {
  return (
    <div className="sticky top-0 z-30 border-b border-sage/60 bg-ivory/85 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3 sm:px-6">
        <NavLink to="/" className="flex items-baseline gap-2">
          <span className="font-display text-2xl font-semibold tracking-tight text-forest">DAR</span>
          <span className="text-xs uppercase tracking-[0.22em] text-gold">Careers</span>
        </NavLink>
        <div className="flex items-center gap-3 text-xs text-ink/60">
          <span title={status.llm_available ? `Generation: ${status.generation_model}` : 'No AI key configured'}
            className="hidden sm:inline">{status.llm_available ? (status.llm_consent ? 'AI agent on' : 'AI off (no consent)') : 'Rule-based mode'}</span>
          <NavLink to="/lab" className="rounded-full px-2 py-1 hover:bg-sage/60">Lab</NavLink>
        </div>
      </div>
    </div>
  )
}

function BottomNav() {
  return (
    <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 border-t border-sage/70 bg-ivory/95 backdrop-blur pb-[env(safe-area-inset-bottom)]">
      <ul className="mx-auto flex max-w-3xl justify-around px-2">
        {NAV.map((n) => (
          <li key={n.to}>
            <NavLink to={n.to} end={n.to === '/'}
              className={({ isActive }) => `flex flex-col items-center gap-0.5 px-3 py-2.5 text-[11px] font-medium transition ${isActive ? 'text-forest' : 'text-ink/45 hover:text-forest'}`}>
              {({ isActive }) => (<>
                <span aria-hidden className={`text-lg leading-none ${isActive ? 'text-gold' : ''}`}>{n.icon}</span>
                {n.label}
              </>)}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
