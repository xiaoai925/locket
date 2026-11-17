export interface Source {
  kind: string
  session?: string
  at?: string
  quote?: string
  from?: string[]
}

export interface Memory {
  id: string
  short: string
  type: string
  about: string
  title: string
  path: string
  active: boolean
  importance: number
  created?: string
  valid_from?: string
  valid_to?: string
  confidence?: number
  sensitivity?: string
  tags?: string[]
  supersedes?: string[]
  derived_from?: string[]
  sources?: Source[]
  text?: string
}

export interface Hit extends Memory {
  score: number
  matched: number
}

export interface HistoryEntry {
  sha: string
  date: string
  author: string
  subject: string
  op: string
}

export interface Conflict {
  id: string
  short: string
  status: string
  kind: string
  path: string
  question: string
  members: string[]
}

export interface WhyResult {
  memory: Memory
  sources: Source[]
  supersedes: string[]
  derived_from: string[]
  history: HistoryEntry[]
}

export interface PendingSession {
  session: string
  sha: string
}

export interface ApproveResult {
  merged: boolean
  commit: string | null
  changed: string[]
  conflicts: string[]
}

export interface Finding {
  a: string
  b: string
  kind: string
  question: string
  confidence: number
}

export interface ViewRef {
  name: string
  sha: string
}

export interface ViewResult {
  name: string
  ref: string
  count: number
}

export interface DeviceSettings {
  mode: string
  cloud: { url: string; repo: string; has_token: boolean }
  embedding: { backend: string; model: string }
  privacy: { default_sensitivity: string; auto_sync: boolean; encrypt_secret: boolean }
  modes: string[]
  embed_backends: string[]
}

// What the UI may send back (partial, deep-merged server-side). token is write-only.
export interface DeviceSettingsPatch {
  mode?: string
  cloud?: { url?: string; repo?: string; token?: string }
  embedding?: { backend?: string; model?: string }
  privacy?: { default_sensitivity?: string; auto_sync?: boolean; encrypt_secret?: boolean }
}
