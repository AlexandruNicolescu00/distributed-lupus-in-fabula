# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Frontend Vue 3
# Build multi-stage: build Node → serve con NGINX statico
# ─────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════
# STAGE 1 — builder
# Compila l'app Vue con Vite e produce la cartella dist/
# ══════════════════════════════════════════════════════════════
FROM node:20-alpine AS builder

WORKDIR /app

# Copia prima i file di dipendenze per sfruttare la cache Docker
COPY package.json package-lock.json* ./

# Installa le dipendenze (ci = install riproducibile da lockfile)
RUN npm ci

# Copia il resto del codice sorgente
COPY . .

# Build di produzione Vite.
# NB: l'host del backend NON è più cucito qui a build-time. Viene iniettato a
# RUNTIME da docker-entrypoint.sh in config.js (vedi stage runtime), così la
# stessa immagine vale per qualsiasi dominio (localhost, game.local, ...).
RUN npm run build


# ══════════════════════════════════════════════════════════════
# STAGE 2 — runtime
# Serve i file statici con NGINX minimale
# ══════════════════════════════════════════════════════════════
FROM nginx:1.25-alpine AS runtime

# envsubst (pacchetto gettext) serve all'entrypoint per iniettare la config runtime
RUN apk add --no-cache gettext

# Rimuove la configurazione default di NGINX
RUN rm /etc/nginx/conf.d/default.conf

# Copia la configurazione NGINX per SPA Vue
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copia i file compilati dallo stage builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Template della config runtime + entrypoint che la materializza in config.js
COPY config.template.js /usr/share/nginx/html/config.template.js
COPY docker-entrypoint.sh /docker-entrypoint.d.custom/entrypoint.sh
RUN chmod +x /docker-entrypoint.d.custom/entrypoint.sh

# Utente non-root
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://localhost:3000 || exit 1

# Entrypoint custom: inietta config.js da env var, poi delega a NGINX
ENTRYPOINT ["/docker-entrypoint.d.custom/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]