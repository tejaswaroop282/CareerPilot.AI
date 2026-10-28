# Deploying CareerPilot to AWS (EC2 + Docker Compose, MongoDB Atlas, S3)

This is path A of the spec (section 12.4): one EC2 machine runs the containers, MongoDB lives in Atlas, uploaded files live in S3,
secrets live in SSM Parameter Store. Local development is unchanged (`docker compose up --build` with local Mongo and MinIO).

**Status:** the package below was built and checked locally (compose files parse, Caddyfile validates, safety checks and tests pass,
`scripts/validate_stack.py` passes 9 of 9 on the local stack). It has **not** been run on AWS or Atlas yet; the first deployment is
the real test, and step 8 verifies it with numbers.

Rough time: 60 to 90 minutes the first time. Rough cost: a t3.medium is about 1 to 1.5 US dollars a day while running; Atlas M0 and
S3 at this size are free or pennies. Stop the instance when you are not demoing (step 9).

## 0. What you need
- An AWS account and a MongoDB Atlas account (both free to open).
- The keys you already use: `GROQ_API_KEY` (required), optional `OPENROUTER_API_KEY`, `LANGSMITH_API_KEY`, `JOBS_API_KEY` (Adzuna), `JOOBLE_API_KEY`.
- No domain is needed (see step 6 for HTTPS without one).

## 1. MongoDB Atlas (database)
1. Create a free **M0** cluster, in a region near the EC2 region (for example Mumbai, `ap-south-1`).
2. Database Access: create a user with a strong password (read and write to any database).
3. Network Access: for now allow `0.0.0.0/0` **only until step 4 gives you the server's Elastic IP**, then replace it with that single IP.
4. Connect, "Drivers", copy the `mongodb+srv://...` string. It goes into SSM in step 5 as `MONGODB_URI`.

## 2. S3 bucket (files)
1. S3, Create bucket, name `careerpilot-data-<something-unique>`, region `ap-south-1`, **Block all public access: on**.
2. Remember the name. The application creates nothing else; files are stored under per-user prefixes.

## 3. IAM role for the server
1. IAM, Policies, Create policy, JSON: paste `deploy/iam-policy.json` and replace both `careerpilot-data-CHANGE-ME` with your bucket name. Name it `careerpilot-server`.
2. IAM, Roles, Create role, trusted entity **EC2**, attach that policy, name it `careerpilot-server-role`.
   It can read and write only that bucket, read only `/careerpilot/*` parameters, and write logs. No access keys are ever created or stored.

## 4. The EC2 machine
1. Launch instance: **Ubuntu 24.04**, **t3.medium** (the reranker and embeddings need 4 GB of RAM), 30 GB disk, IAM instance profile `careerpilot-server-role`.
2. Security group: inbound **80** and **443** from anywhere, **22** only from your own IP. Nothing else. The backend (8000) and MCP server (8001) are internal to the Docker network and are not published.
3. Allocate an **Elastic IP** and attach it (so the address survives stops and starts). Put this IP into Atlas Network Access (step 1.3).

## 5. Secrets and settings (SSM Parameter Store)
From any machine with the AWS CLI, or from CloudShell:

```bash
aws ssm put-parameter --region ap-south-1 --type SecureString --overwrite --name /careerpilot/MONGODB_URI   --value 'mongodb+srv://...'
aws ssm put-parameter --region ap-south-1 --type SecureString --overwrite --name /careerpilot/JWT_SECRET    --value "$(openssl rand -hex 32)"
aws ssm put-parameter --region ap-south-1 --type SecureString --overwrite --name /careerpilot/GROQ_API_KEY  --value 'gsk_...'
aws ssm put-parameter --region ap-south-1 --type String       --overwrite --name /careerpilot/S3_BUCKET     --value 'careerpilot-data-<yours>'
aws ssm put-parameter --region ap-south-1 --type String       --overwrite --name /careerpilot/CORS_ORIGINS  --value 'http://<ELASTIC_IP>'
# optional ones, same pattern: OPENROUTER_API_KEY, LANGSMITH_API_KEY (and LANGSMITH_TRACING=true), JOBS_API_KEY, JOOBLE_API_KEY, SITE_ADDRESS
```
Every parameter under `/careerpilot/` becomes a setting of the same name. Rotate a key later by overwriting its parameter and running `bash deploy/fetch_secrets.sh` followed by `docker compose ... up -d`.

**Adzuna:** the free key allows 25 calls in total. The application stops itself at `JOBS_ADZUNA_MAX_CALLS=20`, counts calls in MongoDB (so restarts cannot reset the count), caches each result for 7 days and only asks about India. Leave the key out if you want zero risk.

## 6. Start it
```bash
ssh ubuntu@<ELASTIC_IP>
git clone <your repository> careerpilot && cd careerpilot
bash deploy/ec2-bootstrap.sh
```
The script installs Docker, builds `.env.production` from the template plus SSM, starts the stack, waits for the backend, loads the knowledge bases (`seed_role_kb`, `seed_company_kb`) and runs the validation table (step 8). The first build downloads the embedding and reranker models and takes about 10 to 15 minutes.

**HTTPS without owning a domain:** set the parameter `SITE_ADDRESS` to `<ip-with-dashes>.sslip.io`, for example `13-232-10-5.sslip.io`, and `CORS_ORIGINS` to `https://13-232-10-5.sslip.io`. Caddy then obtains a real certificate automatically. With a domain of your own, point its A record at the Elastic IP and use that name. Plain `:80` works for a quick demo.

**The DevOps role** comes from a copyrighted book and is deliberately not in git or in the images. The server therefore starts with 3 roles (sde, genai, aiml) and 6 companies. To add it, copy your private `data/role_kb/devops.json` to the server, into `data/role_kb/`, and run `docker compose ... exec backend python -m scripts.seed_role_kb`. The bootstrap validation therefore uses `--min-roles 3`; after you add the DevOps seed run it again with the default (4, the spec's bar).

## 7. Day-to-day
- Logs: `docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend` (one JSON object per line). To send them to CloudWatch add `-f docker-compose.cloudwatch.yml`.
- Update: `git pull && docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build`. Data survives: users in Atlas, files in S3, vectors in the `chroma_data` volume.
- Backups: Atlas has automatic backups on paid tiers; for M0 export with `mongodump` before big changes. S3 versioning is optional.
- Rollback: `git checkout <previous commit>` and run the same `up -d --build`.

## 8. Verify with numbers
```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec backend python -m scripts.validate_stack --base http://localhost:8000
```
It prints a PASS/FAIL table: API latency, sign-up and sign-in round trip (and that a request without a token is refused), MongoDB ping,
S3 round trip, knowledge-base counts against the spec's bar, vector store counts, a retrieval spot check, the production-settings
guard and an MCP job search. From your laptop also open `http://<ELASTIC_IP>/` and register a user.

The server refuses to start in production (`APP_ENV=production`) if `JWT_SECRET` is the default or short, `CORS_ORIGINS` mentions localhost,
`MONGODB_URI` points at a local Mongo, or `S3_ENDPOINT_URL` points at MinIO. The error message says which one.

## 9. Cost control
- Stop the instance when not demoing (Elastic IP stays attached; an unattached Elastic IP costs a little).
- Create an AWS Budget alert (for example 10 US dollars a month).
- The free tiers of Groq (200 thousand tokens a day) and OpenRouter limit heavy use; see the README.

## 10. Scaling up later
Move to ECS Fargate (images in ECR via `deploy/push_ecr.sh`, an Application Load Balancer, EFS for Chroma and the model cache,
Secrets Manager) or replace embedded Chroma with Atlas Vector Search or another managed vector store.

## What is not covered
No automatic certificate renewal monitoring beyond Caddy's own, no multi-instance scaling (sessions that are mid-run live in one API process,
which is why the API marks interrupted runs as failed on restart), no WAF. These are reasonable next steps, not needed for a first deployment.

## Deploying with another tool (images only)

Three images are all a deployment tool needs; build them from the repository root:

| Image | Build | Port | Health check | Notes |
|---|---|---|---|---|
| backend | `docker build -f docker/backend.Dockerfile -t careerpilot-backend .` | 8000 | `GET /health` | 3.8 GB on disk, about 1 GB compressed in ECR (CPU torch, both embedding models baked in), non-root |
| mcp | `docker build -f docker/mcp.Dockerfile -t careerpilot-mcp .` | 8001 (`/mcp`) | TCP | ~330 MB, non-root, no torch |
| frontend | `docker build -t careerpilot-frontend frontend` | 80 | `GET /` | nginx; proxies `/api/` to `BACKEND_HOST:BACKEND_PORT` (defaults `backend:8000`) |

- Backend and MCP read the same variables as `.env.production.example`. Set `APP_ENV=production`: the backend then refuses to start on a weak `JWT_SECRET`, localhost CORS, or a local MongoDB/S3 setting.
- Backend also needs `MCP_SERVER_URL=http://<mcp host>:8001/mcp`, `CHROMA_DIR=/data/chroma` and a **persistent volume at `/data/chroma`** (vectors). Seeding is a one-off command after the first start: `python -m scripts.seed_role_kb` and `python -m scripts.seed_company_kb` inside the backend container (the Chroma volume keeps the result).
- Only the frontend (or a load balancer in front of it) is public; backend and MCP stay internal. The browser talks only to the frontend, so CORS is `https://<public address>`.
- Files go to real S3 with `S3_ENDPOINT_URL` empty and credentials from the task/instance role (policy in `deploy/iam-policy.json`).
- Put secrets in the tool's secret store (Parameter Store, Secrets Manager), never in an image. Logs are JSON on stdout.
- Memory: give the backend at least 2 GB (4 GB recommended); first request loads the embedding models.
- The MCP image bakes in the code: rebuild it whenever `app/jobs` changes.

## Reference deployment (what the steps above create)
| Resource | Name | Purpose |
|---|---|---|
| EC2 t3.small (Amazon Linux 2023, 20 GB gp3, IMDSv2 with hop limit 2) | `careerpilot` | Runs caddy, frontend, backend and mcp with Docker Compose in `/opt/careerpilot` |
| Elastic IP | `careerpilot` | Fixed public address; with `<ip-with-dashes>.sslip.io` Caddy obtains a Let's Encrypt certificate |
| Security group | `careerpilot-web` | Inbound 80 and 443 only |
| S3 bucket | `careerpilot-<account-id>-aps1` | `users/`, `global/` (knowledge-base seeds), `deploy/` (bundle); private, encrypted |
| ECR | `careerpilot-backend`, `-mcp`, `-frontend` | Images pulled by the server with its instance role |
| Parameter Store | `/careerpilot/*` | MONGODB_URI, JWT_SECRET, model and LangSmith keys, SITE_ADDRESS, CORS_ORIGINS |
| IAM | `careerpilot-ec2-role` (server), `careerpilot-deploy` (CLI user) | Policies in `deploy/iam-policy.json` and `deploy/deployer-policy.json` (replace the account id and bucket name with yours) |

Everything is tagged `project=careerpilot`. Commands (use `--profile careerpilot --region ap-south-1`; on Git Bash set `MSYS_NO_PATHCONV=1`):
- Pause: `aws ec2 stop-instances --instance-ids <id>`. Resume: `aws ec2 start-instances ...` (containers restart by themselves; wait about 3 minutes).
- Look at the server: `aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript --parameters 'commands=["cd /opt/careerpilot; docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail 50 backend"]'`, then `aws ssm get-command-invocation`.
- Rotate a key without redeploying: `aws ssm put-parameter --name /careerpilot/GROQ_API_KEY --type SecureString --value ... --overwrite` (the app re-reads it within 60 s).
- Update the app: build and push new images (`deploy/push_ecr.sh`), then on the server `docker compose ... pull && docker compose ... up -d`. Rollback: set `IMAGE_TAG` in `.env.production` to the previous tag and `up -d`.

Final cleanup (console, as account owner): terminate the instance, release the Elastic IP, delete the bucket (empty it first), the three ECR repositories, the `/careerpilot/*` parameters and the log group, then delete the IAM user and the role.

Lessons from the first deployment: shell scripts edited on Windows must have LF line endings; the server role reads parameters by name (no list-by-path permission); a private role seed that is not in git is not on the server.
