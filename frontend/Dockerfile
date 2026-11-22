# ---- Stage 1: build the React app into static files ----
FROM node:20-alpine AS build
WORKDIR /app

# Dependency manifests first, so this layer is cached until they change
COPY package*.json ./
RUN npm ci

COPY . .

# Addresses the BROWSER will use. Baked into the bundle at build time.
ARG VITE_API_URL=http://localhost:8000
ARG VITE_KEYCLOAK_URL=http://localhost:8081
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_KEYCLOAK_URL=$VITE_KEYCLOAK_URL
RUN npm run build

# ---- Stage 2: serve only the built files with nginx ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
