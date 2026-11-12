.PHONY: up down migrate seed test eval simulate lint api e2e-tracking
BE := cd backend &&

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
