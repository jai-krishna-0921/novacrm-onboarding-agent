import os
from pathlib import Path

import pytest

from app.config import Settings

WEB_DIST = Path(__file__).parent / "fixtures" / "web_dist"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        provider_mode="fake",
        rocketlane_template_enterprise="tmpl-ent",
        rocketlane_template_growth="tmpl-growth",
        rocketlane_owner_email="owner@novacrm.example",
        rocketlane_pm_email="pm@novacrm.example",
        rocketlane_csm_enterprise="csm-dedicated@novacrm.example",
        rocketlane_csm_growth="csm-pool@novacrm.example",
        call_max_attempts=3,
        call_retry_delay_seconds=0,
        auth_disabled=True,  # the login guard has its own tests (test_auth.py) with it enabled
        web_dist_dir=str(WEB_DIST),  # a stand-in for `npm run build`: tests never depend on web/dist existing
    )


@pytest.fixture
def ent(settings):
    """A completed Enterprise run (project + Slack channel in the fake world)."""
    from test_workflow import ENTERPRISE_CALL, run

    h, rid = run(settings, calls=[ENTERPRISE_CALL])
    assert h.status(rid) == "COMPLETED"
    return h, rid


# ---------------- Postgres-backed store for parity tests (skips when Postgres is unreachable) ----------------
# One database per test session (per xdist worker), emptied before each test: DROP/CREATE DATABASE per test cost
# ~0.1 s each and dominated the parity tests.
PG_BASE = "postgresql+psycopg://postgres:postgres@localhost:5432"


def worker_db(name: str) -> str:
    """Per-xdist-worker database name, so parallel workers never drop each other's database."""
    return f"{name}_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"


@pytest.fixture(scope="session")
def _pg_session_store():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from pytest_socket import enable_socket
    enable_socket()  # session setup runs inside the first requesting test; per-test setup disables again
    db = worker_db("app_test")
    try:
        admin = sqlalchemy.create_engine(f"{PG_BASE}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
            c.exec_driver_sql(f"CREATE DATABASE {db}")
        admin.dispose()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    from app.db.store import PgStore
    s = PgStore(f"{PG_BASE}/{db}")
    s.create_all()
    yield s
    s.engine.dispose()


@pytest.fixture
def pg_store(_pg_session_store, socket_enabled):
    """An empty PgStore (all tables truncated, identities and the run sequence restarted)."""
    from app.db.store import metadata, run_seq
    s = _pg_session_store
    names = ", ".join(t.name for t in metadata.sorted_tables)
    with s.engine.begin() as c:
        c.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
        c.exec_driver_sql(f"ALTER SEQUENCE {run_seq.name} RESTART")
    return s
