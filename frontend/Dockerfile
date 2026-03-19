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

# Build di produzione Vite
# Le variabili VITE_* vengono sostituite a build-time;
# se preferisci injection a runtime usa entrypoint.sh (vedi sotto)
ARG VITE_WS_URL=ws://localhost/ws
ENV VITE_WS_URL=${VITE_WS_URL}

RUN npm run build


# ══════════════════════════════════════════════════════════════
# STAGE 2 — runtime
# Serve i file statici con NGINX minimale
# ══════════════════════════════════════════════════════════════
FROM nginx:1.25-alpine AS runtime

# Rimuove la configurazione default di NGINX
RUN rm /etc/nginx/conf.d/default.conf

# Copia la configurazione NGINX per SPA Vue
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copia i file compilati dallo stage builder
COPY --from=builder /app/dist /usr/share/nginx/html

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

CMD ["nginx", "-g", "daemon off;"]