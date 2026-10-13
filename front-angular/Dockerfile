FROM node:24-alpine AS build
WORKDIR /app
RUN npm install --global pnpm@11.25.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY angular.json tsconfig*.json ./
COPY src ./src
COPY public ./public
RUN pnpm build

FROM nginx:stable-alpine AS runtime
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY docker/15-runtime-config.sh /docker-entrypoint.d/15-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/15-runtime-config.sh
COPY --from=build /app/dist/vibix-poc/browser /usr/share/nginx/html
ENV API_UPSTREAM=http://host.docker.internal:8010 API_HOST_HEADER=localhost DATA_MODE=api SITE_ID=2
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
