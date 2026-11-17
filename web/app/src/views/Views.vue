<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { ViewRef } from '../types'

const items = ref<ViewRef[]>([])
const err = ref('')
const ok = ref('')
const loading = ref(true)
const busy = ref(false)

async function load() {
  loading.value = true
  err.value = ''
  try {
    items.value = (await api.views()).views
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

async function regenerate() {
  busy.value = true
  err.value = ''
  ok.value = ''
  try {
    const r = await api.generateViews()
    ok.value = r.results.length
      ? '已生成：' + r.results.map((v) => `${v.name}（${v.count} 条）`).join('、')
      : '还没有定义视图；在 .pmf/views.toml 里定义后再生成。'
    await load()
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
    else err.value = '生成失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="section-title">
    <h1>分享视图</h1>
    <span class="count">{{ items.length }}</span>
  </div>
  <p class="muted small">视图是从长期记忆按规则生成的<strong>只读</strong>分享子集，用来把部分记忆分享给他人或某个 AI。secret 级记忆永不进入视图。</p>

  <div class="toolbar" style="margin: 10px 0 14px">
    <button class="primary" :disabled="busy" @click="regenerate">
      {{ busy ? '生成中…' : '生成 / 重新生成视图' }}
    </button>
  </div>
  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="ok" class="small" style="color: var(--accent)">{{ ok }}</div>
  <div v-if="loading" class="muted">加载中…</div>

  <template v-else-if="items.length">
    <div v-for="v in items" :key="v.name" class="card">
      <div class="row spread">
        <strong>{{ v.name }}</strong>
        <span class="mono muted">{{ v.sha }}</span>
      </div>
      <div class="small muted" style="margin-top: 4px">refs/heads/view/{{ v.name }}</div>
    </div>
  </template>
  <p v-else class="muted">还没有生成任何视图。在 <span class="mono">.pmf/views.toml</span> 里定义规则后点上方按钮。</p>
</template>
