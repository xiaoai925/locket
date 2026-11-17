import type { Memory } from './types'

export interface Namespace {
  key: string
  label: string
}

// Top-level memory namespaces (spec §3 repo layout), used to group the list.
export const NAMESPACES: Namespace[] = [
  { key: 'self', label: '关于你' },
  { key: 'people', label: '人物' },
  { key: 'projects', label: '项目' },
  { key: 'world', label: '通用知识' },
  { key: 'procedures', label: '方法 / 流程' },
  { key: 'episodes', label: '经历' },
  { key: 'other', label: '其它' },
]

export function namespaceOf(m: Memory): string {
  const p = m.path || ''
  if (p.startsWith('procedures/')) return 'procedures'
  if (p.startsWith('episodes/')) return 'episodes'
  const a = m.about || ''
  if (a === 'self' || p.startsWith('self/')) return 'self'
  if (a.startsWith('person:') || p.startsWith('people/')) return 'people'
  if (a.startsWith('project:') || p.startsWith('projects/')) return 'projects'
  if (a === 'world' || p.startsWith('world/')) return 'world'
  return 'other'
}

export const TYPES = ['fact', 'preference', 'event', 'procedure', 'summary', 'relation']
