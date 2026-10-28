#!/usr/bin/env bash
# Pack the few files the server needs (no source code, no secrets: the images come from ECR, secrets from Parameter Store).
#   bash deploy/make_bundle.sh /tmp/bundle.tgz
set -euo pipefail
cd "$(dirname "$0")/.."
tar czf "${1:-/tmp/bundle.tgz}" docker-compose.prod.yml docker-compose.cloudwatch.yml .env.production.example \
  deploy/Caddyfile deploy/ec2-bootstrap.sh deploy/fetch_secrets.sh
tar tzf "${1:-/tmp/bundle.tgz}"
