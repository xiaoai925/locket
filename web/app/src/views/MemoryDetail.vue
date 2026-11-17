<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { api, ApiError } from '../api'
import type { WhyResult } from '../types'
import MetaBadges from '../components/MetaBadges.vue'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const route = useRoute()
const router = useRouter()
const id = route.params.id as string

const data = ref<WhyResult | null>(null)
const err = ref('')
const loading = ref(true)

const bodyHtml = computed(() => (data.value?.memory.text ? md.render(data.value.memory.text) : ''))

async function load() {
  loading.value = true
  try {
    data.value = await api.why(id)
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

async function forget() {
  if (!data.value) return
  if (!confirm('确定遗忘这条记忆？（历史中仍可找回）')) return
  try {
    await api.forget(data.value.memory.short)
    router.push({ name: 'memories' })
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <p><RouterLink to="/">← 返回</RouterLink></p>
  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="loading" class="muted">加载中…</div>

  <template v-else-if="data">
    <div class="row spread">
      <h1 style="margin-bottom: 6px">{{ data.memory.title || '（无标题）' }}</h1>
      <span class="mono muted">{{ data.memory.short }}</span>
    </div>
    <MetaBadges :m="data.memory" />
    <div v-if="data.memory.valid_from || data.memory.valid_to" class="small muted" style="margin-top: 6px">
      有效期：{{ data.memory.valid_from || '…' }} 至 {{ data.memory.valid_to || '今' }}
    </div>

    <div class="card md" style="margin-top: 14px" v-html="bodyHtml" />

    <h2>来源</h2>
    <div class="card">
      <div v-for="(s, i) in data.sources" :key="i" class="small" style="margin-bottom: 6px">
        <span class="badge">{{ s.kind }}</span>
        <span class="muted mono">{{ s.session || (s.from && s.from.join(', ')) || '' }}</span>
        <span v-if="s.at" class="muted"> · {{ s.at.slice(0, 16) }}</span>
        <div v-if="s.quote" style="margin-top: 2px">“{{ s.quote }}”</div>
      </div>
      <div v-if="!data.sources.length" class="muted small">无来源记录</div>
    </div>

    <h2>认知演变（版本时间线）</h2>
    <ul class="timeline">
      <li v-for="e in data.history" :key="e.sha">
        <div class="row" style="gap: 8px">
          <span class="badge accent">{{ e.op || '—' }}</span>
          <span>{{ e.subject }}</span>
        </div>
        <div class="small muted mono">{{ e.date }} · {{ e.sha }} · {{ e.author }}</div>
      </li>
    </ul>

    <div style="margin-top: 20px">
      <button class="danger" @click="forget">遗忘这条记忆</button>
    </div>
  </template>
</template>
