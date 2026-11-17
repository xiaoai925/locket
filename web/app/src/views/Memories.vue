<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { api, ApiError } from '../api'
import { NAMESPACES, TYPES, namespaceOf } from '../ns'
import type { Memory } from '../types'
import MemoryCard from '../components/MemoryCard.vue'

const items = ref<Memory[]>([])
const err = ref('')
const loading = ref(true)
const typeFilter = ref('')
const includeInactive = ref(false)

async function load() {
  loading.value = true
  err.value = ''
  try {
    const r = await api.memories({
      type: typeFilter.value || undefined,
      include_inactive: includeInactive.value,
    })
    items.value = r.memories
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

const groups = computed(() =>
  NAMESPACES.map((ns) => ({
    ns,
    items: items.value.filter((m) => namespaceOf(m) === ns.key),
  })).filter((g) => g.items.length),
)
</script>

<template>
  <div class="section-title">
    <h1>记忆</h1>
    <span class="count">共 {{ items.length }} 条</span>
  </div>

  <div class="toolbar">
    <select v-model="typeFilter" @change="load">
      <option value="">全部类型</option>
      <option v-for="t in TYPES" :key="t" :value="t">{{ t }}</option>
    </select>
    <label class="small row" style="gap: 6px">
      <input type="checkbox" style="width: auto" v-model="includeInactive" @change="load" />含已失效
    </label>
  </div>

  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="loading" class="muted">加载中…</div>
  <template v-else-if="items.length">
    <section v-for="g in groups" :key="g.ns.key">
      <h2>{{ g.ns.label }} <span class="count">{{ g.items.length }}</span></h2>
      <MemoryCard v-for="m in g.items" :key="m.id" :m="m" />
    </section>
  </template>
  <p v-else class="muted">还没有记忆。</p>
</template>
