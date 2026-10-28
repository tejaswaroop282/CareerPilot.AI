#!/usr/bin/env bash
# One-time setup on a fresh EC2 instance (Ubuntu 22.04/24.04 or Amazon Linux 2023, t3.medium or larger, 30 GB disk).
# Run as a normal sudo-capable user from the cloned repository:   bash deploy/ec2-bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null; then
  . /etc/os-release
  case "$ID" in
    ubuntu|debian) sudo apt-get update -y && sudo apt-get install -y ca-certificates curl git awscli python3 && curl -fsSL https://get.docker.com | sudo sh ;;
    amzn)          sudo dnf install -y docker git python3 && sudo systemctl enable --now docker
                   sudo mkdir -p /usr/local/lib/docker/cli-plugins
                   sudo curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)" -o /usr/local/lib/docker/cli-plugins/docker-compose
                   sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose ;;
    *) echo "unsupported OS: $ID" >&2; exit 1 ;;
  esac
  sudo usermod -aG docker "${USER:-ec2-user}" || true
fi
sudo systemctl enable --now docker

# 2 GB swap: embedding the knowledge bases peaks near the RAM of a t3.small; swap turns an out-of-memory kill into a slow step
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 1. settings: template + secrets from SSM (or edit .env.production yourself and skip this)
bash deploy/fetch_secrets.sh || echo "SSM step skipped: edit .env.production by hand, then re-run this script"

# 2. start the stack
# pull the images from ECR with the instance role (no keys on the server)
REG="$(grep '^ECR_REGISTRY=' .env.production | cut -d= -f2)"
aws ecr get-login-password --region "${AWS_REGION:-ap-south-1}" | sudo docker login --username AWS --password-stdin "$REG"
sudo docker compose --env-file .env.production -f docker-compose.prod.yml pull
sudo docker compose --env-file .env.production -f docker-compose.prod.yml up -d

# 3. wait for the backend, then load the knowledge bases (one time; safe to repeat)
for i in $(seq 1 40); do
  sudo docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend curl -fs http://localhost:8000/health >/dev/null && break
  sleep 5
done
sudo docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend python -m scripts.seed_role_kb
sudo docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend python -m scripts.seed_company_kb

# 4. check everything with numbers
sudo docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend python -m scripts.validate_stack --base http://localhost:8000 --min-roles 3   # use 4 once the private devops seed is loaded
echo "done. Open http://<your-elastic-ip>/ (or your SITE_ADDRESS)."
