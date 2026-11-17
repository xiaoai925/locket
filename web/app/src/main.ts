import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api'
import { auth } from './auth'
import './styles.css'

const app = createApp(App)
app.use(router)

setUnauthorizedHandler(() => {
  auth.user = null
  if (router.currentRoute.value.name !== 'login') {
    router.push({ name: 'login' })
  }
})

app.mount('#app')
