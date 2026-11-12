"""`make seed`: AE directory (from DEMO_AE_* settings), LangGraph checkpoint tables, and the migration-validation
demo databases (mig_source / mig_dest). Idempotent; also runs on every container start."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.store import PgStore
from app.services.store import Store

DEMO_DATABASES = {"mig_source", "mig_dest"}
FIRST = ["Ava", "Ben", "Chloe", "Dev", "Elena", "Farah", "Gil", "Hana", "Ivan", "Jia"]
LAST = ["Shah", "Okafor", "Novak", "Reyes", "Tanaka"]
BROKEN_EMAIL_ID, DUPLICATE_ID = 17, 23


def demo_contacts() -> tuple[list[dict], list[dict]]:
    """50 source contacts; the destination copy has one row with a missing email and one duplicated row."""
    source = [{"id": i, "name": f"{FIRST[(i - 1) % 10]} {LAST[(i - 1) // 10]}",
               "email": f"{FIRST[(i - 1) % 10].lower()}.{LAST[(i - 1) // 10].lower()}@globex.example"}
              for i in range(1, 51)]
    dest = [{**r, "email": None} if r["id"] == BROKEN_EMAIL_ID else dict(r) for r in source]
    dest.append(dict(source[DUPLICATE_ID - 1]))
    return source, dest


def _ensure_database(url: str) -> None:
    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")  # CREATE DATABASE: no txn
    try:
        with admin.connect() as c:
            if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": u.database}).scalar():
                c.exec_driver_sql(f'CREATE DATABASE "{u.database}"')
    finally:
        admin.dispose()


def _seed_table(url: str, rows: list[dict], primary_key: bool) -> int:
    """Create contacts if missing and fill it only when empty, so a hand-fixed destination survives restarts."""
    _ensure_database(url)
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            # The destination has no PK on purpose: duplicates are what the checker must catch.
            c.exec_driver_sql("CREATE TABLE IF NOT EXISTS contacts (id integer"
                              f"{' PRIMARY KEY' if primary_key else ''}, name text, email text)")
            if c.execute(text("SELECT count(*) FROM contacts")).scalar():
                return 0
            c.execute(text("INSERT INTO contacts (id, name, email) VALUES (:id, :name, :email)"), rows)
            return len(rows)
    finally:
        eng.dispose()


def seed_migration_demo(source_url: str, dest_url: str) -> tuple[int, int]:
    source, dest = demo_contacts()
    return _seed_table(source_url, source, True), _seed_table(dest_url, dest, False)


def seed_ae(store: Store, s: Settings) -> str:
    """Insert the DEMO_AE_* account only if absent: once it exists the dashboard owns the row, and a restart must
    not undo its edits (name, phone, deactivation)."""
    if not (s.demo_ae_email and s.demo_ae_phone):
        return "DEMO_AE_EMAIL / DEMO_AE_PHONE not set: AE directory left unchanged"
    if store.add_ae(s.demo_ae_email, s.demo_ae_name or s.demo_ae_email, s.demo_ae_phone, True):
        return f"AE directory: added {s.demo_ae_email} -> ***{s.demo_ae_phone[-4:]}"
    return f"AE directory: {s.demo_ae_email} already present, left unchanged"


def main() -> None:
    s = Settings()
    print(seed_ae(PgStore(s.database_url), s))

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(s.database_url.replace("+psycopg", "")) as saver:
        saver.setup()
    print("LangGraph checkpoint tables ready")

    # Demo data only goes into the demo-named databases, never into a real customer database.
    if {make_url(s.mig_source_url).database, make_url(s.mig_dest_url).database} != DEMO_DATABASES:
        print("MIG_SOURCE_URL / MIG_DEST_URL are not the demo databases: migration demo data not seeded")
        return
    try:
        added = seed_migration_demo(s.mig_source_url, s.mig_dest_url)
        print(f"migration demo contacts ready (rows inserted source/dest: {added[0]}/{added[1]})")
    except Exception as e:  # noqa: BLE001 - the demo data must never stop the app from starting
        print(f"WARNING: migration demo data not seeded: {type(e).__name__}: {str(e).splitlines()[0]}")


if __name__ == "__main__":
    main()
