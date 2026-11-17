<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { PendingSession } from '../types'

const items = ref<PendingSession[]>([])
const err = ref('')
const ok = ref('')
const loading = ref(true)
const busy = ref('')

async function load() {
  loading.value = true
  err.value = ''
  try {
    items.value = (await api.pending()).pending
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

async function approve(s: PendingSession) {
  busy.value = s.session
  err.value = ''
  ok.value = ''
  try {
    const r = await api.approve(s.session)
    ok.value = r.merged
      ? `已合并 ${r.changed.length} 处变更${r.conflicts.length ? `，记录 ${r.conflicts.length} 个冲突` : ''}`
      : '该会话没有需要合并的变更'
    await load()
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
    else err.value = '批准失败'
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="section-title">
    <h1>合并审批</h1>
    <span class="count">{{ items.length }}</span>
  </div>
  <p class="muted small">会话分支相当于 Agent 提交的合并请求；仅在开启 main_requires_approval 时才需要人工批准。</p>

  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="ok" class="small" style="color: var(--accent)">{{ ok }}</div>
  <div v-if="loading" class="muted">加载中…</div>

  <template v-else-if="items.length">
    <div v-for="s in items" :key="s.session" class="card">
      <div class="row spread">
        <strong>{{ s.session }}</strong>
        <span class="mono muted">{{ s.sha }}</span>
      </div>
      <div class="toolbar" style="margin-top: 10px">
        <button class="primary" :disabled="busy === s.session" @click="approve(s)">
          {{ busy === s.session ? '合并中…' : '批准合并' }}
        </button>
      </div>
    </div>
  </template>
  <p v-else class="muted">没有待审批的会话；仅在开启 main_requires_approval 时才会有。</p>
</template>
