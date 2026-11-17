import type {
  ApproveResult, Conflict, DeviceSettings, DeviceSettingsPatch, Finding, Hit, Memory,
  PendingSession, ViewRef, ViewResult, WhyResult,
} from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  })
  if (res.status === 401) {
    onUnauthorized()
    throw new ApiError(401, '未登录或登录已过期')
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const msg = (data && typeof data === 'object' && 'error' in data && (data as { error: string }).error) || `HTTP ${res.status}`
    throw new ApiError(res.status, msg)
  }
  return data as T
}

export const api = {
  // ---- auth ----
  me: () => request<{ user: string }>('GET', '/auth/me'),
  loginToken: (token: string) => request<{ user: string }>('POST', '/auth/token', { token }),
  logout: () => request<null>('POST', '/auth/logout'),

  // ---- memories ----
  memories: (p: { about?: string; type?: string; include_inactive?: boolean } = {}) => {
    const q = new URLSearchParams()
    if (p.about) q.set('about', p.about)
    if (p.type) q.set('type', p.type)
    if (p.include_inactive) q.set('include_inactive', 'true')
    const qs = q.toString()
    return request<{ memories: Memory[]; count: number }>('GET', '/api/memories' + (qs ? `?${qs}` : ''))
  },
  memory: (id: string) => request<{ memory: Memory }>('GET', `/api/memory/${encodeURIComponent(id)}`),
  why: (id: string) => request<WhyResult>('GET', `/api/memory/${encodeURIComponent(id)}/why`),
  recall: (query: string, opts: { k?: number; about?: string; include_inactive?: boolean } = {}) =>
    request<{ hits: Hit[]; open_conflicts: Conflict[] }>('POST', '/api/recall', { query, ...opts }),
  conflicts: (status = 'open') =>
    request<{ conflicts: Conflict[] }>('GET', `/api/conflicts?status=${encodeURIComponent(status)}`),
  resolve: (id: string, note?: string) =>
    request<{ conflict: Conflict }>('POST', `/api/conflict/${encodeURIComponent(id)}/resolve`, { note }),
  remember: (body: Record<string, unknown>) =>
    request<{ memory: Memory; similar: Memory[]; merged: boolean }>('POST', '/api/remember', body),
  forget: (id: string, reason?: string) =>
    request<unknown>('POST', `/api/memory/${encodeURIComponent(id)}/forget`, { reason }),

  // ---- merge approval ----
  pending: () => request<{ pending: PendingSession[] }>('GET', '/api/pending'),
  approve: (session: string) => request<ApproveResult>('POST', '/api/approve', { session }),

  // ---- semantic conflict scan ----
  detectConflicts: () => request<{ findings: Finding[] }>('GET', '/api/detect-conflicts'),

  // ---- shareable views ----
  views: () => request<{ views: ViewRef[] }>('GET', '/api/views'),
  generateViews: (name?: string) =>
    request<{ results: ViewResult[] }>('POST', '/api/views/generate', name ? { name } : {}),

  // ---- device settings ----
  getSettings: () => request<{ settings: DeviceSettings }>('GET', '/api/settings'),
  updateSettings: (patch: DeviceSettingsPatch) =>
    request<{ settings: DeviceSettings }>('POST', '/api/settings', patch),
}
