<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { auth, logout } from './auth'

const router = useRouter()
const route = useRoute()

async function onLogout() {
  await logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <nav v-if="auth.user && route.name !== 'login'" class="nav">
    <span class="brand">🔒 Locket</span>
    <RouterLink to="/">记忆</RouterLink>
    <RouterLink to="/recall">检索</RouterLink>
    <RouterLink to="/conflicts">冲突</RouterLink>
    <RouterLink to="/pending">审批</RouterLink>
    <RouterLink to="/views">视图</RouterLink>
    <RouterLink to="/settings">设置</RouterLink>
    <span class="spacer" />
    <span class="user">{{ auth.user }}</span>
    <button class="small" @click="onLogout">登出</button>
  </nav>
  <main class="app-shell">
    <RouterView />
  </main>
</template>
