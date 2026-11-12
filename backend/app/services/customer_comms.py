"""Agent 2 (Communication) owns every customer-facing message.

Other agents decide *that* the customer should hear something (e.g. Agent 3: a phase is complete); this module
checks the text and posts it to the run's customer channel under the `comms` agent's tool permissions. Agent 3
has no tool that can reach a customer channel."""

from app.guardrails.output import check_customer_message
from app.services.audit import audit
from app.services.store import Store
from app.tools.registry import ToolFailed, ToolRegistry

AGENT = "comms"


def post_customer_update(store: Store, registry: ToolRegistry, run: dict, text: str, *, key: str,
                         requested_by: str, reason: str) -> None:
    """Raises ToolFailed if the text fails the customer-message check or the post fails."""
    if problems := check_customer_message(text):
        audit(store, run["id"], AGENT, "customer_update", status="blocked", input={"requested_by": requested_by},
              rationale=f"not posted to the customer channel: {problems}")
        raise ToolFailed("slack_post_message", f"customer message check failed: {problems}")
    registry.call(AGENT, "slack_post_message", {"channel_id": run["slack_channel_id"], "text": text},
                  run_id=run["id"], idempotency_key=key,
                  rationale=f"customer update requested by {requested_by}: {reason}")
