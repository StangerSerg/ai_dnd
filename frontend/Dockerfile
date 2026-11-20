# Образ фронтенда AI DnD: сборка Vite, раздача через nginx.
# Контекст сборки — папка frontend/:
#   docker compose build web

FROM node:24-alpine AS build
WORKDIR /app

# Слой зависимостей кэшируется, пока не менялись package*.json.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80