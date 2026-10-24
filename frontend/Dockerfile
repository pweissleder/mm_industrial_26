# syntax=docker/dockerfile:1
FROM node:24-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .

EXPOSE 4321
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
