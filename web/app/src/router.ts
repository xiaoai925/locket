import { createRouter, createWebHistory } from 'vue-router'
import { auth, loadUser } from './auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    { path: '/', name: 'memories', component: () => import('./views/Memories.vue') },
    { path: '/recall', name: 'recall', component: () => import('./views/Recall.vue') },
    { path: '/m/:id', name: 'memory', component: () => import('./views/MemoryDetail.vue') },
    { path: '/conflicts', name: 'conflicts', component: () => import('./views/Conflicts.vue') },
    { path: '/pending', name: 'pending', component: () => import('./views/Pending.vue') },
    { path: '/views', name: 'views', component: () => import('./views/Views.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (!auth.ready) await loadUser()
  if (to.name !== 'login' && !auth.user) return { name: 'login' }
  if (to.name === 'login' && auth.user) return { name: 'memories' }
  return true
})

export default router
