.PHONY: dev test build deploy install

# ── Install all dependencies ──────────────────────────────────────────────────
install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

# ── Development servers (backend + frontend in parallel) ──────────────────────
dev:
	@echo "Starting backend on :8080 and frontend on :5173"
	cd backend && uvicorn main:app --reload --port 8080 &
	cd frontend && npm run dev

# ── Run all tests ─────────────────────────────────────────────────────────────
test:
	cd backend && pytest -v
	cd agents  && pytest -v

# ── Build frontend + Docker image ─────────────────────────────────────────────
build:
	cd frontend && npm run build
	docker build -t golden-hour-backend ./backend

# ── One-command Cloud Run deploy ──────────────────────────────────────────────
deploy:
	bash infra/deploy.sh

# ── Demo simulator ────────────────────────────────────────────────────────────
demo:
	python demo/incident_simulator.py --speed 1.0

demo-fast:
	python demo/incident_simulator.py --speed 2.0

# ── Seed Secret Manager secrets ──────────────────────────────────────────────
secrets:
	bash infra/secrets.sh
