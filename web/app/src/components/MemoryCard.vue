<script setup lang="ts">
import type { Memory } from '../types'
import MetaBadges from './MetaBadges.vue'
defineProps<{ m: Memory }>()
</script>

<template>
  <RouterLink :to="`/m/${m.short}`" class="card clickable" style="display: block; color: inherit">
    <div class="row spread">
      <strong>{{ m.title || '（无标题）' }}</strong>
      <span class="mono muted">{{ m.short }}</span>
    </div>
    <div v-if="m.text" class="small" style="margin: 4px 0 8px">{{ m.text }}</div>
    <MetaBadges :m="m" />
  </RouterLink>
</template>
