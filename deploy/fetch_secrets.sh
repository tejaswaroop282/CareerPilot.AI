#!/usr/bin/env bash
# Build .env.production = the non-secret template + the parameters stored under /careerpilot/ in SSM Parameter Store.
# Parameter names become variable names: /careerpilot/GROQ_API_KEY -> GROQ_API_KEY. Needs the instance role from iam-policy.json.
# Parameters are requested by name (ssm:GetParameters on /careerpilot/*), so the role needs no "list path" permission.
#   Store a secret:  aws ssm put-parameter --name /careerpilot/GROQ_API_KEY --type SecureString --value '...' --overwrite
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-ap-south-1}"
NAMES=(MONGODB_URI MONGODB_DB JWT_SECRET GROQ_API_KEY OPENROUTER_API_KEY LANGSMITH_API_KEY LANGSMITH_TRACING
       SITE_ADDRESS CORS_ORIGINS JOBS_API_KEY JOOBLE_API_KEY GITHUB_TOKEN S3_BUCKET)
cp -n .env.production.example .env.production 2>/dev/null || true
if ! command -v aws >/dev/null; then echo "aws cli not found: install it or edit .env.production by hand" >&2; exit 1; fi
for ((i = 0; i < ${#NAMES[@]}; i += 10)); do
  args=(); for n in "${NAMES[@]:i:10}"; do args+=("/careerpilot/$n"); done
  aws ssm get-parameters --region "$REGION" --with-decryption --names "${args[@]}" \
    --query "Parameters[*].[Name,Value]" --output text | while IFS=$'\t' read -r name value; do
    key="${name##*/}"
    [ -z "$key" ] && continue
    # write the value without letting special characters in it break sed
    python3 - "$key" "$value" <<'PY'
import sys
key, value = sys.argv[1], sys.argv[2]
lines = open(".env.production", encoding="utf-8").read().splitlines()
if any(l.startswith(f"{key}=") for l in lines):
    lines = [f"{key}={value}" if l.startswith(f"{key}=") else l for l in lines]
else:
    lines.append(f"{key}={value}")
open(".env.production", "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
  done
done
chmod 600 .env.production
echo "wrote .env.production ($(grep -c '=' .env.production) settings); secrets came from SSM, nothing was printed"
