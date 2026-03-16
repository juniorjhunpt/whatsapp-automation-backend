FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache python3 make g++ vips-dev git
COPY package*.json ./
RUN npm install
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-alpine
WORKDIR /app
RUN apk add --no-cache vips-dev git
COPY package*.json ./
RUN npm install --production
COPY --from=builder /app/dist ./dist
RUN mkdir -p sessions
EXPOSE 3001
CMD ["node", "dist/index.js"]
