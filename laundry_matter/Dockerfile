FROM node:22.11.0-alpine3.20
WORKDIR /app
COPY package.json package-lock.json /app/
RUN npm ci --omit=dev --no-audit --no-fund
COPY appliance.mjs laundry.mjs /app/
CMD ["node", "/app/laundry.mjs"]
