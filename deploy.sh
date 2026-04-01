#!/usr/bin/env bash
# ── deploy.sh — Manual GCP Deployment Script ─────────────────────────────────
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Prerequisites:
#   1. gcloud CLI installed and authenticated (gcloud auth login)
#   2. GCP project set (gcloud config set project YOUR_PROJECT_ID)
#   3. Required APIs enabled (run Section 1 once)

set -euo pipefail

# ── Configuration — edit these ────────────────────────────────────────────────
PROJECT_ID="your-gcp-project-id"          # e.g. my-project-123456
REGION="us-central1"                       # Cloud Run region
SERVICE_NAME="documind"
IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${SERVICE_NAME}/${SERVICE_NAME}"

LLM_PROVIDER="groq"                        # "groq" or "gemini"
# Store your actual API key in GCP Secret Manager, NOT here.
# Secret must be named "groq-api-key" or "gemini-api-key" in Secret Manager.

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "${CYAN}[deploy]${NC} $1"; }
ok()  { echo -e "${GREEN}✓${NC} $1"; }
warn(){ echo -e "${YELLOW}⚠${NC} $1"; }

# ── Section 1: Enable APIs (run once) ─────────────────────────────────────────
enable_apis() {
  log "Enabling required GCP APIs…"
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    --project="${PROJECT_ID}"
  ok "APIs enabled."
}

# ── Section 2: Create Artifact Registry repo (run once) ───────────────────────
create_registry() {
  log "Creating Artifact Registry repository…"
  gcloud artifacts repositories create "${SERVICE_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" || warn "Repository may already exist — continuing."
  ok "Registry ready."
}

# ── Section 3: Store API key in Secret Manager (run once) ─────────────────────
store_secret() {
  log "Storing LLM API key in Secret Manager…"
  echo "Paste your GROQ (or Gemini) API key and press Enter, then Ctrl+D:"
  gcloud secrets create groq-api-key \
    --data-file=- \
    --project="${PROJECT_ID}" || warn "Secret may already exist."
  ok "Secret stored."
}

# ── Section 4: Build & push Docker image ──────────────────────────────────────
build_and_push() {
  TAG="${IMAGE_REPO}:$(git rev-parse --short HEAD 2>/dev/null || echo latest)"
  TAG_LATEST="${IMAGE_REPO}:latest"

  log "Building Docker image…"
  docker build -t "${TAG}" -t "${TAG_LATEST}" .

  log "Configuring Docker auth for Artifact Registry…"
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

  log "Pushing image…"
  docker push "${TAG}"
  docker push "${TAG_LATEST}"
  ok "Image pushed: ${TAG}"
}

# ── Section 5: Deploy to Cloud Run ────────────────────────────────────────────
deploy() {
  TAG_LATEST="${IMAGE_REPO}:latest"

  log "Deploying to Cloud Run…"
  gcloud run deploy "${SERVICE_NAME}" \
    --image="${TAG_LATEST}" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --memory=2Gi \
    --cpu=2 \
    --min-instances=0 \
    --max-instances=5 \
    --timeout=120 \
    --set-env-vars="LLM_PROVIDER=${LLM_PROVIDER}" \
    --update-secrets="GROQ_API_KEY=groq-api-key:latest" \
    --project="${PROJECT_ID}"

  URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format="value(status.url)")

  echo ""
  echo -e "${GREEN}════════════════════════════════════════${NC}"
  echo -e "${GREEN}  ✓ Deployment complete!                ${NC}"
  echo -e "${GREEN}  URL: ${URL}                           ${NC}"
  echo -e "${GREEN}════════════════════════════════════════${NC}"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
  log "Starting DocuMind deployment to GCP Cloud Run…"
  log "Project: ${PROJECT_ID} | Region: ${REGION} | Service: ${SERVICE_NAME}"
  echo ""

  # Uncomment the lines you need on first deployment:
  # enable_apis
  # create_registry
  # store_secret

  build_and_push
  deploy
}

main "$@"