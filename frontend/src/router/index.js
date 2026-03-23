import { createRouter, createWebHistory } from 'vue-router'
import HomeView  from '../views/HomeView.vue'
import LobbyView from '../views/LobbyView.vue'
import GameView  from '../views/GameView.vue'
import ResultVIew from '@/views/ResultVIew.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/',      name: 'home',  component: HomeView  },
    { path: '/lobby', name: 'lobby', component: LobbyView },
    { path: '/game',  name: 'game',  component: GameView  },
    { path: '/result',  name: 'result',  component: ResultVIew  },

  ],
})

export default router
