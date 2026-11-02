FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json index.html styles.css ./
COPY scripts/build.mjs ./scripts/build.mjs
COPY src ./src
RUN npm run build

FROM node:22-alpine
ENV NODE_ENV=production HOST=0.0.0.0 PORT=50112 BACKEND_URL=http://host.docker.internal:50111
WORKDIR /app
COPY --from=build --chown=node:node /app/dist ./dist
COPY --chown=node:node server.mjs ./server.mjs
USER node
EXPOSE 50112
CMD ["node", "server.mjs"]
