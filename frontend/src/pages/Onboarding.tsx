import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStatus } from '../App'
import { api } from '../api/client'
import { ErrorNote, Pill, Spinner } from '../components/ui'

const CHIP_LABEL: Record<string, string> = {
  dont_know: "I don't know", flexible: "I'm flexible", help_me_decide: 'Help me decide', non_negotiable: 'This is non-negotiable',
  depends: 'It depends', skip: 'Skip',
}

export default function Onboarding() {
  const { status, refresh } = useStatus()
  const stage = status?.onboarding_stage
  return stage === 'upload' ? <Upload onDone={refresh} /> : <Chat onStageChange={refresh} />
}

function Upload({ onDone }: { onDone: () => Promise<void> }) {
  const { status } = useStatus()
  const [consent, setConsent] = useState(status?.llm_consent ?? false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const provider = status?.llm_provider === 'anthropic' ? 'Anthropic' : 'Nebius Token Factory'

  async function send(f: File) {
    setBusy(true); setErr(null)
    try {
      await api.post('/consent', { llm_consent: consent })
      await api.upload('/cv/upload', f)
      await onDone()
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <motion.main initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto max-w-xl px-5 pb-16 pt-10">
      <p className="mb-2 text-xs font-medium uppercase tracking-[0.22em] text-gold">Welcome to DAR Careers</p>
      <h1 className="text-4xl leading-tight sm:text-5xl">Your next chapter.</h1>
      <p className="mt-3 text-ink/70">Less searching. More possibilities. Share your CV and I'll take on the searching, sorting and drafting - you stay in charge of every decision.</p>

      <div onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) send(f) }}
        className={`card mt-8 flex flex-col items-center gap-4 border-2 border-dashed p-8 text-center transition ${drag ? 'border-gold bg-gold/5' : 'border-sage'}`}>
        {busy ? <Spinner label="Reading your CV and checking every fact against it…" /> : (<>
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-sage text-2xl text-forest">↑</div>
          <div>
            <div className="font-medium text-forest">Drop your CV here</div>
            <div className="text-sm text-ink/60">PDF, DOCX or plain text · up to 5 MB</div>
          </div>
          <button className="btn-primary" onClick={() => input.current?.click()}>Choose a file</button>
          <input ref={input} type="file" accept=".pdf,.docx,.txt,.md" className="sr-only" aria-label="Upload CV"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f) }} />
        </>)}
      </div>

      <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl bg-white/70 p-4 text-sm text-ink/75">
        <input type="checkbox" className="mt-1 h-4 w-4 accent-[#304839]" checked={consent} onChange={(e) => setConsent(e.target.checked)}
          disabled={!status?.llm_available} />
        <span>
          {status?.llm_available
            ? <>Use AI to understand my CV and answers. Your CV text will be sent to <b>{provider}</b> ({status?.generation_model}) for processing. It's never sent to employers or job boards.</>
            : <>AI processing isn't configured, so I'll use rule-based extraction. Nothing leaves this machine except job-board searches.</>}
          <span className="mt-1 block text-xs text-ink/50">You don't need to tell me about any diagnosis. You can delete all your data at any time.</span>
        </span>
      </label>
      {err && <div className="mt-4"><ErrorNote>{err}</ErrorNote></div>}
    </motion.main>
  )
}

type Msg = { id: number; role: 'agent' | 'user'; content: string; slot?: string; meta?: any }

function Chat({ onStageChange }: { onStageChange: () => Promise<void> }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const nav = useNavigate()
  const end = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { api.get('/onboarding').then((d) => { setMsgs(d.messages); if (d.stage === 'review') nav('/review') }) }, [nav])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); inputRef.current?.focus() }, [msgs])

  const current = [...msgs].reverse().find((m) => m.role === 'agent' && m.slot)
  const progress = current?.meta ? Math.min(1, (current.meta.index - 1) / current.meta.core_total) : 0

  async function answer(t: string, quick?: string) {
    if (busy || (!t.trim() && !quick)) return
    setBusy(true); setErr(null)
    const optimistic: Msg = { id: Date.now(), role: 'user', content: t || CHIP_LABEL[quick!] }
    setMsgs((m) => [...m, optimistic]); setText('')
    try {
      const d = await api.post('/onboarding/message', { text: t, quick_reply: quick ?? null })
      setMsgs(d.messages)
      if (d.stage !== 'chat') { await onStageChange(); nav('/review') }
    } catch (e: any) { setErr(e.message); setMsgs((m) => m.filter((x) => x !== optimistic)) } finally { setBusy(false) }
  }

  async function later() {
    await api.post('/onboarding/finish-later'); await onStageChange(); nav('/review')
  }

  return (
    <main className="mx-auto flex h-[calc(100dvh-57px)] max-w-xl flex-col px-4 sm:px-5">
      <div className="pt-5">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl">Let's get to know your goals</h1>
          <button className="btn-ghost text-xs" onClick={later}>Review &amp; finish later</button>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-sage/60" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <motion.div className="h-full bg-gold" animate={{ width: `${Math.max(4, progress * 100)}%` }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} />
        </div>
        {current?.meta && <p className="mt-1.5 text-xs text-ink/50">{current.meta.optional ? 'Optional question' : `Question ${current.meta.index} of ${current.meta.core_total}`} · one at a time, no wrong answers</p>}
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto py-5" aria-live="polite">
        <AnimatePresence initial={false}>
          {msgs.map((m) => (
            <motion.div key={m.id} initial={{ opacity: 0, y: 8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-3xl px-4 py-2.5 text-[15px] leading-relaxed ${m.role === 'user' ? 'rounded-br-md bg-forest text-ivory' : 'rounded-bl-md bg-white shadow-[var(--shadow-soft)]'} ${m.meta?.ack ? 'text-sm text-ink/70' : ''}`}>
                {m.content}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
        {busy && <Spinner label="Thinking about that…" />}
        <div ref={end} />
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="sticky bottom-0 bg-ivory pb-5 pt-2">
        {current?.meta?.chips && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {current.meta.chips.filter((c: string) => c !== 'skip' || current.meta.optional || true).map((c: string) => (
              <button key={c} className="chip border-sage bg-white text-forest hover:border-gold" disabled={busy}
                onClick={() => c === 'non_negotiable' ? (text.trim() ? answer(text, c) : inputRef.current?.focus()) : answer(text, c)}>
                {CHIP_LABEL[c]}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={(e) => { e.preventDefault(); answer(text) }} className="flex gap-2">
          <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your answer…" aria-label="Your answer"
            className="flex-1 rounded-full border border-sage bg-white px-5 py-3 text-[15px] outline-none focus:border-gold" />
          <button className="btn-primary" disabled={busy || !text.trim()}>Send</button>
        </form>
        <p className="mt-2 text-center text-[11px] text-ink/45">Tip: type a value and tap "This is non-negotiable" to make it a hard requirement. <Pill tone="plain">Enter ↵</Pill></p>
      </div>
    </main>
  )
}
