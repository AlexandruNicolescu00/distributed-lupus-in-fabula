<!-- src/components/RoleIcon.vue -->
<script setup>
import wolf from '@/assets/wolf_icon.png'
import seer from '@/assets/seer_icon.png'
import villager from '@/assets/villager_icon.png'

const props = defineProps({
  role: { type: String, required: true },
  size: { type: Number, default: 32 },
})
const map = { WOLF: wolf, SEER: seer, VILLAGER: villager }
</script>

<template>
  <img
    :src="map[(role || '').toUpperCase()]"
    :width="size" :height="size"
    style="object-fit: contain"
    alt=""
  />
</template>