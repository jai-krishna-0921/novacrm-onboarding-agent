.PHONY: up down migrate seed test eval simulate lint api e2e-tracking web-install web-dev web-build web-test web-lint check
BE := cd backend &&
WEB := cd web &&

up:        ; docker compose up -d --wait
down:      ; docker compose down
migrate:   ; $(BE) uv run alembic upgrade head
seed:      ; $(BE) uv run python -m app.db.seed
test:      ; $(BE) PROVIDER_MODE=fake uv run pytest -n 4   # 4 xdist workers (~8 s vs ~14 s serial); stress suite: -m stress
eval:      ; $(BE) uv run python -m evals.run_evals $(ARGS)
simulate:  ; $(BE) PROVIDER_MODE=fake uv run python -m scripts.simulate $(SCENARIO)
lint:      ; $(BE) uv run ruff check . && uv run mypy app
api:       ; $(BE) uv run uvicorn app.main:app --reload --port 8000
env-check: ; $(BE) uv run python -m scripts.env_check
preflight: ; $(BE) uv run python -m scripts.preflight
gmail-auth: ; $(BE) uv run python -m scripts.gmail_auth
retell-setup: ; $(BE) uv run python -m scripts.retell_setup $(ARGS)
e2e-tracking: ; $(BE) uv run python -m scripts.e2e_tracking

# Dashboard (web/: React + Vite, served by the API at /ui from web/dist)
web-install: ; $(WEB) npm ci
web-dev:   ; $(WEB) npm run dev     # :5173/ui/, proxies the API to make api on :8000
web-build: ; $(WEB) npm run build   # -> web/dist (what /ui serves)
web-test:  ; $(WEB) npm run test
web-lint:  ; $(WEB) npm run lint
check: test web-lint web-test web-build   # everything offline: backend tests + dashboard lint/test/build
