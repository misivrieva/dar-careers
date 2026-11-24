import { useCallback, useEffect, useRef, useState } from 'react'

/** Fetch now, then poll while `active` returns true for the latest data. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, active: (d: T | null) => boolean = () => true) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const activeRef = useRef(active)
  activeRef.current = active

  const load = useCallback(async () => {
    try {
      const d = await fnRef.current()
      setData(d)
      setError(null)
      return d
    } catch (e: any) {
      setError(e.message || 'Something went wrong')
      return null
    }
  }, [])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const d = await load()
      if (!alive) return
      if (activeRef.current(d)) timer.current = window.setTimeout(tick, ms)
    }
    tick()
    return () => {
      alive = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [load, ms])

  return { data, error, reload: load, setData }
}
