FROM node:20-slim AS builder
WORKDIR /app
RUN apt-get update -qq && apt-get install -y -qq python3 make g++ git libvips-dev 2>/dev/null && rm -rf /var/lib/apt/lists/*
COPY package*.json ./
RUN npm install
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-slim
WORKDIR /app
RUN apt-get update -qq && apt-get install -y -qq libvips 2>/dev/null && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
RUN mkdir -p sessions
EXPOSE 3001
CMD ["node", "dist/index.js"]
