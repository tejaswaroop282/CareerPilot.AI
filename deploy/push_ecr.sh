#!/usr/bin/env bash
# Optional (spec 12.4): build the three images and push them to Amazon ECR, tagged with the git commit.
# Usage: AWS_ACCOUNT_ID=123456789012 AWS_REGION=ap-south-1 bash deploy/push_ecr.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"; REGION="${AWS_REGION:-ap-south-1}"
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
REG="$AWS_ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
for pair in "backend:-f docker/backend.Dockerfile ." "mcp:-f docker/mcp.Dockerfile ." "frontend:frontend"; do
  name="${pair%%:*}"; args="${pair#*:}"
  aws ecr describe-repositories --region "$REGION" --repository-names "careerpilot-$name" >/dev/null 2>&1 \
    || aws ecr create-repository --region "$REGION" --repository-name "careerpilot-$name" --tags Key=project,Value=careerpilot >/dev/null
  docker build -t "$REG/careerpilot-$name:$TAG" $args
  docker push "$REG/careerpilot-$name:$TAG"
  docker tag "$REG/careerpilot-$name:$TAG" "$REG/careerpilot-$name:latest" && docker push "$REG/careerpilot-$name:latest"
  echo "pushed careerpilot-$name:$TAG"
done
