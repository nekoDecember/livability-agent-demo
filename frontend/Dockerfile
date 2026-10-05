# syntax=docker/dockerfile:1

# Compose passes BuildKit's predefined proxy args for npm RUN commands. Do not
# redeclare them with ARG or ENV, so proxy values stay out of image history.

FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx-entrypoint.sh /usr/local/bin/livability-nginx-entrypoint

RUN chmod 0555 /usr/local/bin/livability-nginx-entrypoint

USER nginx
ENTRYPOINT ["/usr/local/bin/livability-nginx-entrypoint"]

EXPOSE 80
