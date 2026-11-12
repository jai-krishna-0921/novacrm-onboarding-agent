"""`make seed`: AE directory (from DEMO_AE_* settings), LangGraph checkpoint tables, and the migration-validation
demo databases (mig_source / mig_dest). Idempotent; also runs on every container start."""

from datetime import date, timedelta

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.store import PgStore
from app.services.migration_check import Table
from app.services.migration_demo import insert_rows
from app.services.store import Store

DEMO_DATABASES = {"mig_source", "mig_dest"}
FIRST = ["Ava", "Ben", "Chloe", "Dev", "Elena", "Farah", "Gil", "Hana", "Ivan", "Jia", "Kofi", "Lena"]
LAST = ["Shah", "Okafor", "Novak", "Reyes", "Tanaka"]
COMPANIES = ["Globex", "Globex Retail", "Globex Logistics", "Globex Health"]
TITLES = ["VP Sales", "Account Manager", "Ops Lead", "CFO", "Analyst", "", "Director, IT"]
STAGES = ["customer", "customer", "lead", "opportunity"]

# The old CRM export (source) vs what landed in NovaCRM (destination). Planted defects, all real SQL data:
DROPPED_IDS = (12, 31, 47, 58)          # never copied: row count short, primary keys missing
DUPLICATE_ID = 23                       # copied twice (destination has no primary key)
BLANK_EMAIL_IDS, BLANK_NAME_ID = (17, 44), 36   # required fields lost in transit
SOURCE_COLUMNS = ["id", "name", "email", "phone", "company", "title", "created_on"]
DEST_COLUMNS = ["id", "name", "email", "company", "title", "created_on", "lifecycle_stage"]  # phone dropped
SOURCE_DDL = ("id integer PRIMARY KEY, name text, email text, phone text, company text, title text, "
              "created_on date")
DEST_DDL = "id integer, name text, email text, company text, title text, created_on date, lifecycle_stage text"


def demo_contacts() -> tuple[Table, Table]:
    """60 source contacts (7 columns, some optional values blank) and the 57-row destination copy: no phone
    column, an extra lifecycle_stage column, 4 rows never copied, 1 duplicated, 2 blank emails, 1 blank name."""
    source: list[dict] = []
    for i in range(1, 61):
        first, last = FIRST[(i - 1) % 12], LAST[(i - 1) // 12]
        source.append({"id": i, "name": f"{first} {last}", "email": f"{first.lower()}.{last.lower()}@globex.example",
                       "phone": None if i % 9 == 0 else f"+1 415 555 {1000 + i:04d}",
                       "company": COMPANIES[i % 4], "title": TITLES[i % 7] or None,
                       "created_on": date(2024, 1, 1) + timedelta(days=11 * i)})
    dest = []
    for r in source:
        i = r["id"]
        if i in DROPPED_IDS:
            continue
        d = {c: r.get(c) for c in DEST_COLUMNS[:-1]} | {"lifecycle_stage": STAGES[i % 4]}
        if i in BLANK_EMAIL_IDS:
            d["email"] = None
        if i == BLANK_NAME_ID:
            d["name"] = ""
        dest.append(d)
        if i == DUPLICATE_ID:
            dest.append(dict(d))
    return Table(source, SOURCE_COLUMNS), Table(dest, DEST_COLUMNS)


def _ensure_database(url: str) -> None:
    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")  # CREATE DATABASE: no txn
    try:
        with admin.connect() as c:
            if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": u.database}).scalar():
                c.exec_driver_sql(f'CREATE DATABASE "{u.database}"')
    finally:
        admin.dispose()


def _columns(url: str) -> list[str]:
    _ensure_database(url)
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            insp = inspect(c)
            return [col["name"] for col in insp.get_columns("contacts")] if insp.has_table("contacts") else []
    finally:
        eng.dispose()


def _seed_table(url: str, ddl: str, rows: Table, rebuild: bool) -> int:
    """Create contacts if missing and fill it only when empty, so a hand-fixed destination survives restarts.
    rebuild: drop it first (the demo layout changed since it was seeded)."""
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            if rebuild:
                c.exec_driver_sql("DROP TABLE IF EXISTS contacts")
            c.exec_driver_sql(f"CREATE TABLE IF NOT EXISTS contacts ({ddl})")
            if c.execute(text("SELECT count(*) FROM contacts")).scalar():
                return 0
            insert_rows(c, rows)
            return len(rows)
    finally:
        eng.dispose()


def seed_migration_demo(source_url: str, dest_url: str) -> tuple[int, int]:
    source, dest = demo_contacts()
    have = _columns(source_url)
    _columns(dest_url)  # creates the database
    rebuild = bool(have) and have != SOURCE_COLUMNS  # an older demo layout: rebuild both sides
    # The destination has no PK on purpose: duplicates are what the checker must catch.
    return _seed_table(source_url, SOURCE_DDL, source, rebuild), _seed_table(dest_url, DEST_DDL, dest, rebuild)


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
