"""Slack provider (slack_sdk WebClient). docs/reference/slack-api.md."""

from typing import Any

from slack_sdk.errors import SlackApiError

from app.tools.definitions import SlackInviteSharedOut
from app.tools.registry import PermanentToolError, TransientToolError

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

    def _call(self, fn, *, ok_errors: frozenset[str] = frozenset(), **kw) -> dict | None:
        try:
            return fn(**kw)
        except (TimeoutError, ConnectionError, OSError) as e:  # urllib URLError is an OSError
            raise TransientToolError(f"slack transport: {e}") from e
        except SlackApiError as e:
            code = e.response.get("error", "unknown") if e.response is not None else "unknown"
            if code in ok_errors:
                return None
            status = getattr(e.response, "status_code", 200)
            if code in ("ratelimited", "internal_error", "fatal_error", "service_unavailable") or status >= 500 \
                    or status == 429:
                raise TransientToolError(f"slack {code}") from e
            raise PermanentToolError(code) from e

    def create_channel(self, name: str) -> str:
        return self._call(self.c.conversations_create, name=name, is_private=False)["channel"]["id"]

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
        self._call(self.c.chat_postMessage, channel=channel_id, text=text)
