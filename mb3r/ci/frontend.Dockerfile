# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
FROM node:24-slim AS builder
WORKDIR /app
ENV CYPRESS_INSTALL_BINARY=0 NEXT_TELEMETRY_DISABLED=1
# Next.js embeds addresses at build time; application handlers are unchanged.
ENV RECOMMENDATION_ADDR=recommendation:9001 PRODUCT_CATALOG_ADDR=product-catalog:3550
ENV AD_ADDR=unused:9555 CART_ADDR=unused:7070 CHECKOUT_ADDR=unused:5050 CURRENCY_ADDR=unused:7001 SHIPPING_ADDR=unused:50051 PRODUCT_REVIEWS_ADDR=unused:3551
COPY src/frontend/package*.json ./
RUN npm ci
COPY src/frontend/ ./
RUN npm run build
FROM node:24-slim
WORKDIR /app
COPY --from=builder /app/.next/standalone/ ./
COPY --from=builder /app/.next/static/ .next/static/
COPY --from=builder /app/node_modules/ node_modules/
COPY --from=builder /app/public/ public/
COPY --from=builder /app/utils/telemetry/Instrumentation.js Instrumentation.js
CMD ["node", "--require=./Instrumentation.js", "server.js"]
