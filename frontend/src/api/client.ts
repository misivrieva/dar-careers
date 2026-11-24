export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, headers: {} }
  if (body !== undefined) {
    if (isForm) init.body = body as FormData
    else {
      ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
  }
  const r = await fetch(`/api${path}`, init)
  if (!r.ok) {
    let msg = r.statusText
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* ignore */ }
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  get: <T = any>(p: string) => req<T>('GET', p),
  post: <T = any>(p: string, b?: unknown) => req<T>('POST', p, b ?? {}),
  patch: <T = any>(p: string, b: unknown) => req<T>('PATCH', p, b),
  del: <T = any>(p: string) => req<T>('DELETE', p),
  upload: <T = any>(p: string, f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    return req<T>('POST', p, fd, true)
  },
}

export const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/-/g, '')

export const money = (n?: number | null, cur = 'GBP') =>
  n == null ? '' : new Intl.NumberFormat('en-GB', { style: 'currency', currency: cur || 'GBP', maximumFractionDigits: 0, notation: n >= 100000 ? 'compact' : 'standard' }).format(n)

export function salaryText(s: any): string | null {
  if (!s) return null
  const c = s.currency || 'GBP'
  if (s.min && s.max && s.min !== s.max) return `${money(s.min, c)} – ${money(s.max, c)}`
  return money(s.max || s.min, c)
}
