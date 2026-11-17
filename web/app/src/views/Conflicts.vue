<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Conflict, Finding } from '../types'

const items = ref<Conflict[]>([])
const findings = ref<Finding[]>([])
const err = ref('')
const loading = ref(true)
const scanning = ref(false)
const scanned = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = (await api.conflicts('open')).conflicts
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

async function resolve(c: Conflict) {
  const note = prompt('解决说明（可选）：', '')
  if (note === null) return
  try {
    await api.resolve(c.short, note || undefined)
    await load()
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
  }
}

async function scan() {
  scanning.value = true
  err.value = ''
  try {
    findings.value = (await api.detectConflicts()).findings
    scanned.value = true
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
  } finally {
    scanning.value = false
  }
}

function short(id: string) {
  return id.slice(-8).toLowerCase()
}

onMounted(load)
</script>

<template>
  <div class="section-title">
    <h1>待确认的冲突</h1>
    <span class="count">{{ items.length }}</span>
  </div>

  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="loading" class="muted">加载中…</div>

  <template v-else-if="items.length">
    <div v-for="c in items" :key="c.id" class="card">
      <div class="row spread">
        <strong>{{ c.question || c.kind }}</strong>
        <span class="mono muted">{{ c.short }}</span>
      </div>
      <div class="small muted" style="margin: 4px 0 10px">
        <span class="badge">{{ c.kind }}</span>
        <span v-if="c.path" class="mono"> {{ c.path }}</span>
      </div>
      <button @click="resolve(c)">标记为已解决</button>
    </div>
  </template>
  <p v-else class="muted">没有待确认的冲突 🎉</p>

  <div class="section-title" style="margin-top: 28px">
    <h2>扫描疑似重复 / 矛盾</h2>
  </div>
  <p class="muted small">只读预览，不会改动记忆。需在 .pmf/config.toml 开启 <span class="mono">policy.semantic_conflicts = "heuristic"</span>。</p>
  <div class="toolbar" style="margin: 10px 0 14px">
    <button class="primary" :disabled="scanning" @click="scan">
      {{ scanning ? '扫描中…' : '扫描' }}
    </button>
  </div>

  <template v-if="findings.length">
    <div v-for="(f, i) in findings" :key="i" class="card">
      <div class="row spread">
        <strong>{{ f.question }}</strong>
        <span class="badge" :class="{ warn: f.kind === 'contradiction' }">{{ f.kind }}</span>
      </div>
      <div class="small muted mono" style="margin-top: 4px">
        {{ short(f.a) }} × {{ short(f.b) }} · 置信度 {{ f.confidence }}
      </div>
    </div>
  </template>
  <p v-else-if="scanned" class="muted">未发现疑似重复或矛盾。</p>
</template>
