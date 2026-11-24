import { useState } from 'react'

/** Editable list of short tags with optional suggestions. Enter or comma adds; × removes. */
export function TagInput({ label, values, onChange, suggestions = [], placeholder, tone = 'forest' }: {
  label: string; values: string[]; onChange: (v: string[]) => void; suggestions?: string[]; placeholder?: string; tone?: 'forest' | 'clay'
}) {
  const [text, setText] = useState('')
  const has = (v: string) => values.some((x) => x.toLowerCase() === v.toLowerCase())
  const add = (raw: string) => {
    const v = raw.trim().replace(/,$/, '').slice(0, 60)
    if (v && !has(v)) onChange([...values, v])
    setText('')
  }
  const chip = tone === 'clay' ? 'border-clay/30 bg-clay/10 text-clay' : 'border-forest bg-forest text-ivory'
  const open = suggestions.filter((s) => !has(s) && (!text || s.toLowerCase().includes(text.toLowerCase()))).slice(0, 8)
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-ink/60">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5 rounded-2xl border border-sage bg-white p-2 focus-within:border-gold">
        {values.map((v) => (
          <span key={v} className={`chip ${chip}`}>
            {v}
            <button type="button" aria-label={`Remove ${v}`} className="ml-0.5 opacity-70 hover:opacity-100" onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
          </span>
        ))}
        <input value={text} placeholder={values.length ? '' : placeholder} aria-label={label}
          onChange={(e) => (e.target.value.endsWith(',') ? add(e.target.value) : setText(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); add(text) }
            if (e.key === 'Backspace' && !text && values.length) onChange(values.slice(0, -1))
          }}
          onBlur={() => text && add(text)}
          className="min-w-[8rem] flex-1 bg-transparent px-1 py-1 text-[15px] outline-none" />
      </div>
      {open.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {open.map((s) => (
            <button key={s} type="button" onClick={() => add(s)} className="chip border-sage bg-white text-forest/80 hover:border-gold">+ {s}</button>
          ))}
        </div>
      )}
    </div>
  )
}
