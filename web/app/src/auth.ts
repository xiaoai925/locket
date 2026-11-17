import { reactive } from 'vue'
import { api } from './api'

export const auth = reactive<{ user: string | null; ready: boolean }>({ user: null, ready: false })

export async function loadUser(): Promise<void> {
  try {
    const r = await api.me()
    auth.user = r.user
  } catch {
    auth.user = null
  }
  auth.ready = true
}

export async function logout(): Promise<void> {
  try {
    await api.logout()
  } catch {
    /* ignore */
  }
  auth.user = null
}
