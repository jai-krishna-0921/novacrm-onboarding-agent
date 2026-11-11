.PHONY: up down migrate seed test eval simulate lint api
BE := cd backend &&

up:        ; docker compose up -d --wait
down:      ; docker compose down
migrate:   ; $(BE) uv run alembic upgrade head
seed:      ; $(BE) uv run python -m app.db.seed
test:      ; $(BE) PROVIDER_MODE=fake uv run pytest
eval:      ; $(BE) uv run python -m evals.run_evals
simulate:  ; $(BE) PROVIDER_MODE=fake uv run python -m scripts.simulate $(SCENARIO)
lint:      ; $(BE) uv run ruff check . && uv run mypy app
api:       ; $(BE) uv run uvicorn app.main:app --reload --port 8000
env-check: ; $(BE) uv run python -m scripts.env_check
