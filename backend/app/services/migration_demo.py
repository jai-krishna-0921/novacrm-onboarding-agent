"""Migration demo controls: repair the demo destination (so a re-check passes on camera) or restore its planted
defects. Only ever writes to the configured demo destination (see `refusal`); never touches the source."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.services.migration_check import REQUIRED, Contacts, blank

DEMO_DEST_DATABASE = "mig_dest"


def refusal(s: Settings) -> str | None:
    """Why fix/reset must not run here, or None when they may."""
    if not s.migration_demo_controls:
        return "migration demo controls are disabled (MIGRATION_DEMO_CONTROLS=false)"
    if make_url(s.mig_dest_url).database != DEMO_DEST_DATABASE:
        return f"MIG_DEST_URL is not the demo database {DEMO_DEST_DATABASE!r}; refusing to modify it"
    return None


def repair(source: Contacts, dest: Contacts) -> Contacts:
    """Destination with duplicate ids dropped (first row kept), blank required fields filled from the source row,
    and source rows missing from the destination added. Non-blank destination values are kept. Sorted by id."""
    by_id = {r["id"]: r for r in source}
    out: dict = {}
    for r in dest:
        if r["id"] in out:
            continue
        src = by_id.get(r["id"], {})
        out[r["id"]] = {**r, **{f: src[f] for f in REQUIRED if blank(r.get(f)) and not blank(src.get(f))}}
    for i, r in by_id.items():
        out.setdefault(i, dict(r))
    return sorted(out.values(), key=lambda r: (r["id"] is None, r["id"] if isinstance(r["id"], int) else 0,
                                               str(r["id"])))


def replace_contacts(url: str, rows: Contacts) -> None:
    """Replace the whole `contacts` table in one transaction (a failure leaves it as it was)."""
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            c.execute(text("DELETE FROM contacts"))
            if rows:
                c.execute(text("INSERT INTO contacts (id, name, email) VALUES (:id, :name, :email)"), rows)
    finally:
        eng.dispose()
