"""Composition root: builds store, providers (real|fake), LLM, registry, graph + Postgres checkpointer, runner."""

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.services.runner import Runner
from app.services.store import Store
from app.tools.definitions import AeLookupOut


@dataclass
class Container:
    settings: Settings
    store: Store
    runner: Runner
    inbox: Any = None            # GmailClient-like: current_history_id(), new_deal_emails(cursor), watch(topic)
    start_poller: bool = True
    voice: Any = None            # RetellVoice-like: get_call(call_id) -> call object, for webhook reconciliation
    registry: Any = None         # ToolRegistry, for actions taken outside the graph (migration check)
    migration_contacts: Any = None  # () -> (source_rows, dest_rows) for the migration check
    llm: Any = None              # LLM for the tracking agent's handoff draft (None -> static template)


class DbDirectory:
    def __init__(self, store: Store):
        self.store = store

    def lookup(self, email: str) -> AeLookupOut:
        ae = self.store.lookup_ae(email)
        return AeLookupOut(found=True, **ae) if ae else AeLookupOut(found=False)


@dataclass
class RealProviders:
    directory: Any
    gmail: Any
    voice: Any
    rocketlane: Any
    slack: Any


def build_container(s: Settings) -> Container:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from app.db.store import PgStore
    from app.graph.root import build_graph
    from app.graph.state import Deps
    from app.llm.client import LangChainLLM
    from app.tools.definitions import build_registry

    store = PgStore(s.database_url)
    if s.provider_mode == "real":
        from app.providers.real.gmail import GmailClient, build_service
        from app.providers.real.retell import RetellVoice
        from app.providers.real.rocketlane import RocketlaneClient
        from app.providers.real.slack import SlackClient

        gmail = GmailClient(build_service(s.google_client_id, s.google_client_secret, s.google_refresh_token),
                            s.cs_inbox_address)
        providers = RealProviders(DbDirectory(store), gmail, RetellVoice(s.retell_api_key, s.retell_agent_id,
                                                                         s.retell_from_number),
                                  RocketlaneClient(s.rocketlane_base_url, s.rocketlane_api_key),
                                  SlackClient(s.slack_bot_token))
        inbox, voice = gmail, providers.voice
    else:
        from app.providers.fake import FakeWorld
        providers, inbox, voice = FakeWorld(), None, None

    pool = ConnectionPool(s.database_url.replace("+psycopg", ""), max_size=10, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    checkpointer = PostgresSaver(pool)
    from app.services.migration_check import load_contacts

    registry = build_registry(store, providers)
    llm = LangChainLLM(store, s.llm_model, s.anthropic_api_key)
    deps = Deps(s, store, registry, llm)
    graph = build_graph(deps, checkpointer)
    return Container(settings=s, store=store, runner=Runner(graph, store), inbox=inbox, voice=voice,
                     registry=registry, llm=LangChainLLM(store, s.llm_handoff_model, s.anthropic_api_key),
                     migration_contacts=lambda: (load_contacts(s.mig_source_url), load_contacts(s.mig_dest_url)))
