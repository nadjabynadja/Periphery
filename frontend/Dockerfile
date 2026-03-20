# Stage 1: Build
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install --legacy-peer-deps
COPY . .
ARG VITE_MAPBOX_ACCESS_TOKEN
ARG VITE_API_BASE_URL
ARG VITE_AUTH_ENABLED
ENV VITE_MAPBOX_ACCESS_TOKEN=$VITE_MAPBOX_ACCESS_TOKEN
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_AUTH_ENABLED=$VITE_AUTH_ENABLED
RUN npm run build

# Stage 2: Serve
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
