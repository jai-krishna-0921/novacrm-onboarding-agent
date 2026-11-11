"""Slack provider (slack_sdk WebClient). docs/reference/slack-api.md."""

from typing import Any

from slack_sdk.errors import SlackApiError

from app.tools.definitions import SlackInviteSharedOut
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

CONNECT_UNAVAILABLE = {"not_paid", "restricted_action", "not_allowed_token_type", "team_not_found",
                       "invite_not_allowed", "not_supported", "user_not_found"}


def _client(token: str) -> Any:
    from slack_sdk import WebClient
    from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

    c = WebClient(token=token, timeout=20)
    c.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))
    return c


class SlackClient:
    def __init__(self, token: str = "", client: Any = None):
        self.c = client or _client(token)

    def _call(self, fn, *, ok_errors: frozenset[str] = frozenset(), write: bool = False, **kw) -> dict | None:
        try:
            return fn(**kw)
        except (TimeoutError, ConnectionResetError) as e:
            # The request may have reached Slack: a write may have happened (no idempotency on Slack's side).
            if write:
                raise AmbiguousToolError(f"slack transport after send: {e}") from e
            raise TransientToolError(f"slack transport: {e}") from e
        except (ConnectionError, OSError) as e:  # urllib URLError is an OSError (e.g. connection refused)
            raise TransientToolError(f"slack transport: {e}") from e
        except SlackApiError as e:
            code = e.response.get("error", "unknown") if e.response is not None else "unknown"
            if code in ok_errors:
                return None
            status = getattr(e.response, "status_code", 200)
            if code in ("internal_error", "fatal_error") and write:
                raise AmbiguousToolError(f"slack {code}") from e  # docs: "may have partially succeeded"
            if code in ("ratelimited", "internal_error", "fatal_error", "service_unavailable") or status >= 500 \
                    or status == 429:
                raise TransientToolError(f"slack {code}") from e
            raise PermanentToolError(code) from e

    def create_channel(self, name: str) -> str:
        out = self._call(self.c.conversations_create, write=True, name=name, is_private=False)
        if out is None:  # only possible with ok_errors, which create doesn't pass
            raise PermanentToolError("conversations.create returned nothing")
        return out["channel"]["id"]

    def _bot_user_id(self) -> str:
        if not getattr(self, "_me", None):
            self._me = (self._call(self.c.auth_test) or {})["user_id"]
        return self._me

    def find_channel(self, name: str) -> str | None:
        """Our own channel with this exact name (created by this bot), else None. Scope: channels:read."""
        me, cursor = self._bot_user_id(), None
        while True:
            page = self._call(self.c.conversations_list, types="public_channel", exclude_archived=True,
                              limit=1000, **({"cursor": cursor} if cursor else {})) or {}
            for ch in page.get("channels", []):
                if ch.get("name") == name:
                    return ch["id"] if ch.get("creator") == me else None
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return None

    def find_post(self, channel_id: str, text: str) -> bool:
        """Did this bot already post exactly this text in the channel (recent history)? Scope: channels:history."""
        me = self._bot_user_id()
        page = self._call(self.c.conversations_history, channel=channel_id, limit=200) or {}
        want = _slack_text(text)
        return any(m.get("user") == me and _slack_text(m.get("text", "")) == want for m in page.get("messages", []))

    def set_topic(self, channel_id: str, topic: str) -> None:
        self._call(self.c.conversations_setTopic, channel=channel_id, topic=topic)

    def invite(self, channel_id: str, user_ids: list[str]) -> None:
        self._call(self.c.conversations_invite, ok_errors=frozenset({"already_in_channel"}),
                   channel=channel_id, users=",".join(user_ids))

    def invite_shared(self, channel_id: str, email: str) -> SlackInviteSharedOut:
        try:
            self._call(self.c.conversations_inviteShared, channel=channel_id, emails=[email])
        except PermanentToolError as e:
            if str(e) in CONNECT_UNAVAILABLE:
                return SlackInviteSharedOut(status="unsupported", detail=str(e))
            raise
        return SlackInviteSharedOut(status="sent")

    def post(self, channel_id: str, text: str) -> None:
        self._call(self.c.chat_postMessage, write=True, channel=channel_id, text=text)


def _slack_text(t: str) -> str:
    """Slack stores &, < and > escaped; compare on the unescaped, trimmed text."""
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()
