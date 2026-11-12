"""Migration demo controls: repair the demo destination (so a re-check passes on camera) or restore its planted
defects. Only ever writes to the configured demo destination (see `refusal`); never touches the source."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.services.migration_check import REQUIRED, Contacts, Table, blank, columns_of, compare_columns

DEMO_DEST_DATABASE = "mig_dest"


def refusal(s: Settings) -> str | None:
    """Why fix/reset must not run here, or None when they may."""
    if not s.migration_demo_controls:
        return "migration demo controls are disabled (MIGRATION_DEMO_CONTROLS=false)"
    if make_url(s.mig_dest_url).database != DEMO_DEST_DATABASE:
        return f"MIG_DEST_URL is not the demo database {DEMO_DEST_DATABASE!r}; refusing to modify it"
    return None


def repair(source: Contacts, dest: Contacts) -> Table:
    """Destination with the source columns it lacks added (values copied from the source row), duplicate ids
    dropped (first row kept), blank required fields filled from the source, and source rows missing from the
    destination added. Non-blank destination values and extra destination columns are kept. Sorted by id."""
    by_id = {r["id"]: r for r in source}
    cols = compare_columns(source, dest)
    columns = cols["dest"] + cols["missing"]
    out: dict = {}
    for r in dest:
        if r["id"] in out:
            continue
        src = by_id.get(r["id"], {})
        fill = {f: src[f] for f in REQUIRED if blank(r.get(f)) and not blank(src.get(f))}
        out[r["id"]] = {**r, **{c: src.get(c) for c in cols["missing"]}, **fill}
    for i, r in by_id.items():
        out.setdefault(i, {c: r.get(c) for c in columns})
    return Table(sorted(out.values(), key=lambda r: (r["id"] is None, r["id"] if isinstance(r["id"], int) else 0,
                                                     str(r["id"]))), columns)


def insert_rows(conn, rows: Contacts) -> None:
    if rows:
        cols = columns_of(rows)
        q = conn.dialect.identifier_preparer.quote
        conn.execute(text(f"INSERT INTO contacts ({', '.join(q(c) for c in cols)}) "
                          f"VALUES ({', '.join(':' + c for c in cols)})"), [{c: r.get(c) for c in cols} for r in rows])


def replace_contacts(url: str, rows: Contacts) -> None:
    """Replace the whole `contacts` table in one transaction (a failure leaves it as it was). The table's columns
    are made to match the rows': missing ones are added as text, extra ones dropped (demo database only)."""
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            q = c.dialect.identifier_preparer.quote
            have = [col["name"] for col in inspect(c).get_columns("contacts")]
            want = columns_of(rows) or have
            for col in want:
                if col not in have:
                    c.exec_driver_sql(f"ALTER TABLE contacts ADD COLUMN {q(col)} text")
            for col in have:
                if col not in want:
                    c.exec_driver_sql(f"ALTER TABLE contacts DROP COLUMN {q(col)}")
            c.execute(text("DELETE FROM contacts"))
            insert_rows(c, rows)
    finally:
        eng.dispose()
