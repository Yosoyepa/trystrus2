# Multi-stage build: Node.js build -> Nginx Alpine runtime
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

# Production Nginx container
FROM nginx:1.27-alpine

# Install curl for healthcheck
RUN apk add --no-cache curl

# Copy custom Nginx configuration with reverse proxy routing
COPY nginx.conf /etc/nginx/nginx.conf

# Copy built SPA static assets
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --start-period=3s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
