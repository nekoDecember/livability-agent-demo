#!/bin/sh
set -eu

server_config=/tmp/livability-server.conf
nginx_config=/tmp/nginx.conf

envsubst '${LIVABILITY_API_KEY}' \
  < /etc/nginx/templates/default.conf.template \
  > "$server_config"

mkdir -p \
  /tmp/nginx/client_temp \
  /tmp/nginx/proxy_temp \
  /tmp/nginx/fastcgi_temp \
  /tmp/nginx/uwsgi_temp \
  /tmp/nginx/scgi_temp

cat > "$nginx_config" <<'EOF'
pid /tmp/nginx.pid;
worker_processes auto;
error_log /dev/stderr warn;

events {
  worker_connections 1024;
}

http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  access_log /dev/stdout;
  sendfile on;
  keepalive_timeout 65;
  client_body_temp_path /tmp/nginx/client_temp;
  proxy_temp_path /tmp/nginx/proxy_temp;
  fastcgi_temp_path /tmp/nginx/fastcgi_temp;
  uwsgi_temp_path /tmp/nginx/uwsgi_temp;
  scgi_temp_path /tmp/nginx/scgi_temp;
  include /tmp/livability-server.conf;
}
EOF

exec nginx -c "$nginx_config" -g 'daemon off;'
