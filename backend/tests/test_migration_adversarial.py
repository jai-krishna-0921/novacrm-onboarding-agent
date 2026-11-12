"""Adversarial cases for the schema-aware migration check and the demo repair/replace path (review of 07dfb01)."""

import pytest
from sqlalchemy import create_engine

from app.db.seed import demo_contacts
from app.services.migration_check import Table, evaluate, find_problems, load_contacts
from app.services.migration_demo import repair, replace_contacts


def _passed(source, dest) -> dict:
    return {r.name: r.passed for r in evaluate(source, dest)}


@pytest.mark.parametrize("extra_id", [999, None], ids=["orphan_id", "null_id"])
def test_dashboard_problems_agree_with_check_when_dest_has_extra_rows(extra_id):
    """A destination row whose id is not in the source fails record_count, but find_problems (dashboard summary,
    row tags) has no category for it, so the dashboard shows a clean table while the check FAILS."""
    source, dest = demo_contacts()
    fixed = repair(source, dest)
    fixed.append({**fixed[0], "id": extra_id})
    assert not all(_passed(source, fixed).values())          # the check fails (record_count 60 != 61)
    assert any(find_problems(source, fixed).values()), "dashboard reports no problem for the failing check"


@pytest.mark.parametrize("extra_id", [999, None], ids=["orphan_id", "null_id"])
def test_repair_makes_the_check_pass_when_dest_has_extra_rows(extra_id):
    """Demo 'Fix' must leave a destination the check accepts; repair never removes rows the source lacks."""
    source, dest = demo_contacts()
    dest = Table(list(dest) + [{**dest[0], "id": extra_id}], dest.columns)
    assert all(_passed(source, repair(source, dest)).values()), _passed(source, repair(source, dest))


def test_dest_without_id_column_reports_schema_failure_instead_of_crashing():
    """The schema check exists to report missing columns; a destination missing `id` raises KeyError first
    (not MigrationDataUnavailable), so the tracker's rule b and POST /runs/{id}/migration-check crash."""
    source, dest = demo_contacts()
    no_id = Table([{k: v for k, v in r.items() if k != "id"} for r in dest], [c for c in dest.columns if c != "id"])
    results = {r.name: r for r in evaluate(source, no_id)}
    assert results["schema"].passed is False and "id" in results["schema"].details["sample_ids"]


@pytest.fixture
def pg_dest(socket_enabled):
    from conftest import worker_db

    base = "postgresql+psycopg://postgres:postgres@localhost:5432"
    name = worker_db("mig_adv_dst")
    try:
        admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            conn.exec_driver_sql(f"CREATE DATABASE {name}")
        admin.dispose()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    url = f"{base}/{name}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE contacts (id integer, name text, email text)")
    eng.dispose()
    return url


@pytest.mark.parametrize("col", ["first name", "e-mail-2", "Lifecycle Stage", "2fa"])
def test_replace_contacts_handles_column_names_that_need_quoting(pg_dest, col):
    """Fix copies every source column into the destination. Identifiers are quoted in the ALTER and INSERT
    column list, but the bind parameters are ':' + raw column name, which breaks on spaces/hyphens/leading
    digits (a real CRM export has columns like 'First Name')."""
    rows = Table([{"id": 1, "name": "A", "email": "a@x", col: "v"}], ["id", "name", "email", col])
    replace_contacts(pg_dest, rows)
    got = load_contacts(pg_dest)
    assert got.columns == ["id", "name", "email", col] and got[0][col] == "v"
