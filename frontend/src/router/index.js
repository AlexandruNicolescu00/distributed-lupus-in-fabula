import { createRouter, createWebHistory } from 'vue-router'
import HomeView  from '../views/HomeView.vue'
import LobbyView from '../views/LobbyView.vue'
import GameView  from '../views/GameView.vue'
import ResultView from '@/views/ResultView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/',      name: 'home',  component: HomeView  },
    { path: '/lobby/:id?', name: 'lobby', component: LobbyView },
    { path: '/game/:id?',  name: 'game',  component: GameView  },
    { path: '/results/:id?',  name: 'result',  component: ResultView  },

  ],
})

export default router
