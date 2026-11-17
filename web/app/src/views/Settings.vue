<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, ApiError } from '../api'
import type { DeviceSettings, DeviceSettingsPatch } from '../types'

const s = ref<DeviceSettings | null>(null)
const err = ref('')
const ok = ref('')
const loading = ref(true)
const busy = ref(false)

// editable local copy; token is write-only (never echoed back)
const form = reactive({
  mode: 'local',
  cloudUrl: '',
  cloudRepo: 'memory',
  token: '',
  embedBackend: 'off',
  embedModel: '',
  defaultSensitivity: 'personal',
  autoSync: false,
  encryptSecret: true,
})

const MODE_DESC: Record<string, string> = {
  local: '只用手机本地的记忆，不联网。隐私最高。',
  cloud: '直接使用云上记忆，自动与云端保持一致。换手机无缝。',
  hybrid: '本地为主，联网时与云端同步/互补。',
}
const EMBED_DESC: Record<string, string> = {
  off: '仅关键词检索（BM25），不做语义。',
  local: '本地离线嵌入，浅层语义，手机可跑、不联网。',
  model: '本地模型嵌入，语义更强，需要在设备上配置模型。',
  cloud: '云端嵌入，语义最好，但需要联网并可能产生费用。',
}
const SENSITIVITIES = ['public', 'personal', 'sensitive']

function fill(d: DeviceSettings) {
  s.value = d
  form.mode = d.mode
  form.cloudUrl = d.cloud.url
  form.cloudRepo = d.cloud.repo
  form.token = ''
  form.embedBackend = d.embedding.backend
  form.embedModel = d.embedding.model
  form.defaultSensitivity = d.privacy.default_sensitivity
  form.autoSync = d.privacy.auto_sync
  form.encryptSecret = d.privacy.encrypt_secret
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    fill((await api.getSettings()).settings)
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) err.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  busy.value = true
  err.value = ''
  ok.value = ''
  const patch: DeviceSettingsPatch = {
    mode: form.mode,
    cloud: { url: form.cloudUrl.trim(), repo: form.cloudRepo.trim() || 'memory' },
    embedding: { backend: form.embedBackend, model: form.embedModel.trim() },
    privacy: {
      default_sensitivity: form.defaultSensitivity,
      auto_sync: form.autoSync,
      encrypt_secret: form.encryptSecret,
    },
  }
  if (form.token.trim()) patch.cloud!.token = form.token.trim() // write-only
  try {
    fill((await api.updateSettings(patch)).settings)
    ok.value = '已保存'
  } catch (e) {
    if (e instanceof ApiError) err.value = e.message
    else err.value = '保存失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <h1>设置</h1>
  <p class="muted small">这些设置只保存在本设备，用于决定这台手机如何使用记忆。</p>

  <div v-if="err" class="error">{{ err }}</div>
  <div v-if="loading" class="muted">加载中…</div>

  <template v-else-if="s">
    <h2>记忆来源模式</h2>
    <div class="card">
      <label v-for="m in s.modes" :key="m" class="opt">
        <input type="radio" :value="m" v-model="form.mode" />
        <span>
          <strong>{{ m === 'local' ? '本地' : m === 'cloud' ? '云端' : '混合' }}</strong>
          <span class="small muted"> — {{ MODE_DESC[m] }}</span>
        </span>
      </label>
    </div>

    <h2>云端</h2>
    <div class="card">
      <label class="field">
        <span class="small muted">Gitea 地址</span>
        <input type="url" v-model="form.cloudUrl" placeholder="http://主机:3000" />
      </label>
      <label class="field">
        <span class="small muted">仓库名</span>
        <input type="text" v-model="form.cloudRepo" placeholder="memory" />
      </label>
      <label class="field">
        <span class="small muted">
          访问令牌
          <span class="badge" :class="{ accent: s.cloud.has_token }">
            {{ s.cloud.has_token ? '已配置' : '未配置' }}
          </span>
        </span>
        <input type="password" v-model="form.token" placeholder="仅在更新令牌时填写（不回显）" autocomplete="off" />
      </label>
    </div>

    <h2>Embedding（语义检索）</h2>
    <div class="card">
      <label class="field">
        <span class="small muted">后端</span>
        <select v-model="form.embedBackend">
          <option v-for="b in s.embed_backends" :key="b" :value="b">{{ b }}</option>
        </select>
      </label>
      <p class="small muted" style="margin: 2px 0 0">{{ EMBED_DESC[form.embedBackend] }}</p>
      <label v-if="form.embedBackend === 'model' || form.embedBackend === 'cloud'" class="field" style="margin-top: 10px">
        <span class="small muted">模型（id 或路径）</span>
        <input type="text" v-model="form.embedModel" placeholder="例如模型名或本地路径" />
      </label>
    </div>

    <h2>隐私与同步</h2>
    <div class="card">
      <label class="field">
        <span class="small muted">默认敏感级别</span>
        <select v-model="form.defaultSensitivity">
          <option v-for="lv in SENSITIVITIES" :key="lv" :value="lv">{{ lv }}</option>
        </select>
      </label>
      <label class="opt">
        <input type="checkbox" v-model="form.autoSync" />
        <span><strong>自动同步</strong><span class="small muted"> — 云端/混合模式下，读取前拉取、写入后推送</span></span>
      </label>
      <label class="opt">
        <input type="checkbox" v-model="form.encryptSecret" />
        <span><strong>加密 secret 记忆</strong><span class="small muted"> — 需已配置接收者公钥</span></span>
      </label>
    </div>

    <div class="toolbar" style="margin-top: 18px">
      <button class="primary" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存' }}</button>
      <span v-if="ok" class="small" style="color: var(--accent)">{{ ok }}</span>
    </div>
  </template>
</template>

<style scoped>
.opt { display: flex; gap: 10px; align-items: flex-start; padding: 7px 0; }
.opt input { margin-top: 4px; flex: none; width: auto; }
.field { display: block; margin-bottom: 12px; }
.field > span { display: block; margin-bottom: 4px; }
.field:last-child { margin-bottom: 0; }
</style>
