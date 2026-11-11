#!/usr/bin/env bash
# Ship the committed code + local .env to the EC2 host and (re)start the stack.
# Usage: deploy/deploy.sh <ip>      (run deploy/aws_up.sh first)
# The local .env is copied as-is (scp, never printed); the host's compose file overrides DATABASE_URL,
# PUBLIC_BASE_URL and PROVIDER_MODE. Postgres and admin passwords are generated on the host on first deploy.
set -euo pipefail
IP="${1:?usage: deploy/deploy.sh <ip>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$HOME/.ssh/novacrm-${AWS_REGION:-ap-south-1}.pem"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new ec2-user@"$IP")
DOMAIN="${IP//./-}.sslip.io"

"${SSH[@]}" 'until [ -f /var/lib/cloud/novacrm-ready ]; do echo "waiting for first boot..."; sleep 5; done'

# Code: only what is committed (no .env, no local junk).
git -C "$ROOT" archive --format=tar HEAD backend deploy | "${SSH[@]}" 'mkdir -p /opt/novacrm/src && rm -rf /opt/novacrm/src/* && tar -x -C /opt/novacrm/src'
# Secrets: copied, never echoed.
scp -q -i "$KEY" "$ROOT/.env" ec2-user@"$IP":/opt/novacrm/app.env
"${SSH[@]}" 'chmod 600 /opt/novacrm/app.env'

"${SSH[@]}" DOMAIN="$DOMAIN" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/novacrm
cp src/deploy/docker-compose.prod.yml docker-compose.yml
cp src/deploy/Caddyfile Caddyfile
if [ ! -f .env ]; then
  umask 077
  ADMIN_PW=$(openssl rand -base64 18 | tr -d '/+=')
  HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$ADMIN_PW")
  printf "POSTGRES_PASSWORD=%s\nDOMAIN=%s\nADMIN_PASSWORD_HASH='%s'\n" \
    "$(openssl rand -hex 24)" "$DOMAIN" "$HASH" > .env
  echo "$ADMIN_PW" > admin-password
  echo "generated postgres + admin passwords (admin password in /opt/novacrm/admin-password)"
fi
docker build -q -t novacrm-api:latest src/backend
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
for i in $(seq 1 60); do
  if curl -fsS "https://$DOMAIN/healthz" >/dev/null 2>&1; then echo "healthy: https://$DOMAIN/healthz"; exit 0; fi
  sleep 5
done
echo "not healthy yet; check: docker compose logs api caddy"; exit 1
REMOTE
