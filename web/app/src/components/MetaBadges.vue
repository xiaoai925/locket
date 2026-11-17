<script setup lang="ts">
import type { Memory } from '../types'
defineProps<{ m: Memory }>()
</script>

<template>
  <span class="row" style="gap: 6px">
    <span class="badge accent">{{ m.type }}</span>
    <span class="badge">{{ m.about }}</span>
    <span v-if="m.sensitivity && m.sensitivity !== 'personal'" class="badge warn">{{ m.sensitivity }}</span>
    <span v-for="t in m.tags || []" :key="t" class="badge">#{{ t }}</span>
    <span v-if="!m.active" class="badge expired">已失效</span>
  </span>
</template>
