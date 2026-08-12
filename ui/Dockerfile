FROM node:22-alpine AS build
WORKDIR /ui
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN pnpm install --frozen-lockfile && pnpm run build

FROM nginx:1.27-alpine
COPY --from=build /ui/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
