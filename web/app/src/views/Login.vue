<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { auth } from '../auth'

const router = useRouter()
const token = ref('')
const err = ref('')
const busy = ref(false)

async function loginWithToken() {
  err.value = ''
  if (!token.value.trim()) {
    err.value = '请填写 Gitea 个人令牌'
    return
  }
  busy.value = true
  try {
    const r = await api.loginToken(token.value.trim())
    auth.user = r.user
    auth.ready = true
    router.push({ name: 'memories' })
  } catch (e) {
    err.value = e instanceof ApiError ? e.message : '登录失败'
  } finally {
    busy.value = false
  }
}

function oauth() {
  window.location.href = '/auth/login'
}
</script>

<template>
  <div class="center">
    <div class="login-box">
      <h1>🔒 Locket 记忆</h1>
      <p class="muted small">登录你的云上记忆仓</p>
      <div class="card">
        <button class="primary" style="width: 100%" @click="oauth">用 Gitea 登录</button>
        <div class="muted small" style="text-align: center; margin: 12px 0">— 或开发登录 —</div>
        <input type="text" v-model="token" placeholder="粘贴 Gitea 个人令牌 (PAT)" @keyup.enter="loginWithToken" />
        <div v-if="err" class="error">{{ err }}</div>
        <button style="width: 100%; margin-top: 10px" :disabled="busy" @click="loginWithToken">
          {{ busy ? '登录中…' : '令牌登录' }}
        </button>
      </div>
    </div>
  </div>
</template>
