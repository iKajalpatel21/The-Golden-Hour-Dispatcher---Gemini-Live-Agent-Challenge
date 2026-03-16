#!/usr/bin/env bash
# One-command Cloud Run deploy
# Usage: bash infra/deploy.sh
set -euo pipefail

PROJECT=$(gcloud config get-value project)
REGION="${CLOUD_RUN_REGION:-us-central1}"
IMAGE="gcr.io/${PROJECT}/golden-hour-backend:latest"

echo "▶ Project  : $PROJECT"
echo "▶ Region   : $REGION"
echo "▶ Image    : $IMAGE"

# 1. Build & push Docker image
echo ""
echo "── Building Docker image ──"
docker build -t "$IMAGE" ./backend
docker push "$IMAGE"

# 2. Substitute PROJECT_ID in cloudrun.yaml
sed "s/PROJECT_ID/${PROJECT}/g" infra/cloudrun.yaml > /tmp/cloudrun_deploy.yaml

# 3. Deploy to Cloud Run
echo ""
echo "── Deploying to Cloud Run ──"
gcloud run services replace /tmp/cloudrun_deploy.yaml \
  --region "$REGION" \
  --platform managed

# 4. Make publicly accessible (remove for production)
gcloud run services add-iam-policy-binding golden-hour-backend \
  --region "$REGION" \
  --member="allUsers" \
  --role="roles/run.invoker"

SERVICE_URL=$(gcloud run services describe golden-hour-backend \
  --region "$REGION" \
  --format "value(status.url)")

echo ""
echo "✅ Deployed: $SERVICE_URL"
echo "   Health:   $SERVICE_URL/health"
