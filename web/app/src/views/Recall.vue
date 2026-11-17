<script setup lang="ts">
import { ref } from 'vue'
import { api, ApiError } from '../api'
import type { Hit, Conflict } from '../types'
import MetaBadges from '../components/MetaBadges.vue'

const q = ref('')
const hits = ref<Hit[]>([])
const conflicts = ref<Conflict[]>([])
const err = ref('')
const busy = ref(false)
const searched = ref(false)

function maxScore() {
  return Math.max(1, ...hits.value.map((h) => h.score))
}

async function run() {
  if (!q.value.trim()) return
  busy.value = true
  err.value = ''
  try {
    const r = await api.recall(q.value.trim(), { k: 15 })
    hits.value = r.hits
    conflicts.value = r.open_conflicts
    searched.value = true
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <h1>检索</h1>
  <div class="toolbar">
    <input type="search" v-model="q" placeholder="输入关键词，回车检索" @keyup.enter="run" />
    <button class="primary" :disabled="busy" @click="run">检索</button>
  </div>

  <div v-if="err" class="error">{{ err }}</div>

  <div v-if="conflicts.length" class="card" style="border-color: var(--warn)">
    <strong class="warn">相关的待确认冲突</strong>
    <div v-for="c in conflicts" :key="c.id" class="small" style="margin-top: 6px">{{ c.question }}</div>
  </div>

  <RouterLink
    v-for="h in hits"
    :key="h.id"
    :to="`/m/${h.short}`"
    class="card clickable"
    style="display: block; color: inherit"
  >
    <div class="row spread">
      <strong>{{ h.title || '（无标题）' }}</strong>
      <span class="mono muted">{{ h.score.toFixed(2) }}</span>
    </div>
    <div class="score-bar" :style="{ width: (100 * h.score) / maxScore() + '%', margin: '4px 0 8px' }" />
    <div v-if="h.text" class="small" style="margin-bottom: 8px">{{ h.text }}</div>
    <MetaBadges :m="h" />
  </RouterLink>

  <p v-if="searched && !hits.length" class="muted">没有找到相关记忆。</p>
</template>
