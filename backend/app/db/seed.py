"""`make seed`: AE directory (from DEMO_AE_* settings) + LangGraph checkpoint tables. Idempotent."""

from app.config import Settings
from app.db.store import PgStore


def main() -> None:
    s = Settings()
    store = PgStore(s.database_url)
    if s.demo_ae_email and s.demo_ae_phone:
        store.upsert_ae(s.demo_ae_email, s.demo_ae_name or s.demo_ae_email, s.demo_ae_phone, True)
        print(f"AE directory: {s.demo_ae_email} -> ***{s.demo_ae_phone[-4:]}")
    else:
        print("DEMO_AE_EMAIL / DEMO_AE_PHONE not set: AE directory left unchanged")

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(s.database_url.replace("+psycopg", "")) as saver:
        saver.setup()
    print("LangGraph checkpoint tables ready")


if __name__ == "__main__":
    main()
