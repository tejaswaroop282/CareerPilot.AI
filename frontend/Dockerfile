# Build the Vite app, then serve the static files with nginx.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# A relative /api path is baked in; nginx proxies it to the backend, so no rebuild is needed per environment.
RUN npm run build

FROM nginx:1.27-alpine
# the official image fills ${BACKEND_HOST}/${BACKEND_PORT} into this template at start (defaults match docker compose)
ENV BACKEND_HOST=backend BACKEND_PORT=8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
