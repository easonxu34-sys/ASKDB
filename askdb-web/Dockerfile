FROM node:22-slim

RUN corepack enable

WORKDIR /app
COPY askdb-web/package.json askdb-web/pnpm-lock.yaml ./askdb-web/

WORKDIR /app/askdb-web
RUN corepack pnpm install --frozen-lockfile

COPY askdb-web/ ./
RUN corepack pnpm build

ENV NODE_ENV=production

EXPOSE 3000

CMD ["corepack", "pnpm", "start", "--hostname", "0.0.0.0"]
