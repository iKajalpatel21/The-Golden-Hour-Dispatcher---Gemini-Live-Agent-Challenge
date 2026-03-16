#!/usr/bin/env bash
# Seed all secrets into Google Secret Manager.
# Run once before first deploy: bash infra/secrets.sh
# Reads values from .env in the project root.
set -euo pipefail

# Load .env
if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
else
  echo "ERROR: .env file not found. Copy .env.example → .env and fill in values."
  exit 1
fi

PROJECT="${GOOGLE_CLOUD_PROJECT}"
echo "▶ Seeding secrets for project: $PROJECT"

create_or_update() {
  local name=$1
  local value=$2
  if gcloud secrets describe "$name" --project "$PROJECT" &>/dev/null; then
    echo "   Updating $name"
    echo -n "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT"
  else
    echo "   Creating $name"
    echo -n "$value" | gcloud secrets create "$name" --data-file=- --project "$PROJECT"
  fi
}

create_or_update "gemini-api-key"       "$GEMINI_API_KEY"
create_or_update "elevenlabs-api-key"   "$ELEVENLABS_API_KEY"
create_or_update "elevenlabs-voice-id"  "$ELEVENLABS_VOICE_ID"
create_or_update "gcp-project"          "$GOOGLE_CLOUD_PROJECT"
create_or_update "maps-api-key"         "$MAPS_API_KEY"

echo ""
echo "✅ All secrets seeded."
