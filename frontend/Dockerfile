# Stage 1: Build the frontend
FROM node:20-slim AS frontend-build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_MAPBOX_ACCESS_TOKEN
ARG VITE_API_BASE_URL=""

# Type-check then build with base=/ so assets are served from root (not /app/)
RUN npx tsc && npx vite build --base=/

# Stage 2: Serve with nginx
FROM nginx:alpine AS frontend

COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
