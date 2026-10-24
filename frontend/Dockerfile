# syntax=docker/dockerfile:1
# Targets: `dev` (Astro dev server, used by compose.yaml) and `prod` (static build
# behind nginx, used by compose.prod.yaml). `prod` is the default.

FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# The lockfile copy lets the dev container notice a stale node_modules volume.
RUN --mount=type=cache,target=/root/.npm npm ci && cp package-lock.json node_modules/.image-lock.json

FROM deps AS dev
COPY . .
EXPOSE 4321
# compose keeps node_modules in an anonymous volume that survives `--build`;
# reinstall when package-lock.json has changed since that volume was filled.
CMD ["sh", "-c", "cmp -s package-lock.json node_modules/.image-lock.json || (npm ci && cp package-lock.json node_modules/.image-lock.json); exec npm run dev -- --host 0.0.0.0"]

FROM deps AS build
COPY . .
# Empty: the browser calls /api on the same origin, which nginx proxies.
ARG PUBLIC_API_BASE_URL=
RUN npm run build

FROM nginx:1.29-alpine AS prod
# The official image renders templates with envsubst (only for defined variables).
ENV BACKEND_URL=http://backend:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
