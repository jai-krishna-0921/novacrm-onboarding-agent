"""Gmail provider: read new deal emails via history.list, reply in-thread. docs/reference/gmail-api.md."""

import base64
import html
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from app.tools.definitions import GmailReplyIn, GmailReplyOut, InboundEmail
from app.tools.registry import PermanentToolError, TransientToolError

DEAL_SUBJECT = re.compile(r"closed[\s-]*won|new deal|deal closed", re.I)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def extract_text(payload: dict) -> str:
    """Prefer text/plain anywhere in the MIME tree; otherwise strip text/html."""
    plain, rich = [], []

    def walk(p: dict) -> None:
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mt == "text/plain":
            plain.append(_b64(data))
        elif data and mt == "text/html":
            rich.append(_b64(data))
        for child in p.get("parts") or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", "\n".join(rich), flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def to_inbound(msg: dict) -> InboundEmail:
    h = _headers(msg)
    return InboundEmail(message_id=msg["id"], thread_id=msg["threadId"],
                        rfc_message_id=h.get("message-id", ""), sender=parseaddr(h.get("from", ""))[1].lower(),
                        subject=h.get("subject", ""), body=extract_text(msg.get("payload", {})))


def build_reply(a: GmailReplyIn, sender: str) -> dict:
    m = EmailMessage()
    m["To"], m["From"], m["Subject"] = a.to, sender, a.subject
    if a.rfc_message_id:
        m["In-Reply-To"] = m["References"] = a.rfc_message_id
    m.set_content(a.body)
    return {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode(), "threadId": a.thread_id}


def build_service(client_id: str, client_secret: str, refresh_token: str) -> Any:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(None, refresh_token=refresh_token, client_id=client_id, client_secret=client_secret,
                        token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailClient:
    def __init__(self, service: Any, self_address: str):
        self.service, self.self_address = service, self_address.lower()

    def _exec(self, req) -> dict:
        try:
            return req.execute()
        except Exception as e:  # googleapiclient.errors.HttpError and transport errors
            status = getattr(getattr(e, "resp", None), "status", 0)
            if status == 404:
                raise PermanentToolError("history_too_old") from e
            if status in (0, 429) or status >= 500:
                raise TransientToolError(f"gmail {status}: {e}") from e
            raise PermanentToolError(f"gmail {status}: {e}") from e

    def reply(self, a: GmailReplyIn) -> GmailReplyOut:
        sent = self._exec(self.service.users().messages().send(userId="me", body=build_reply(a, self.self_address)))
        return GmailReplyOut(message_id=sent["id"])

    def current_history_id(self) -> str:
        return str(self._exec(self.service.users().getProfile(userId="me"))["historyId"])

    def watch(self, topic: str) -> dict:
        return self._exec(self.service.users().watch(
            userId="me", body={"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"}))

    def new_deal_emails(self, start_history_id: str) -> tuple[list[InboundEmail], str]:
        """Messages added to INBOX since the cursor, filtered to deal notifications. Returns (emails, new cursor)."""
        ids: list[str] = []
        cursor, token = start_history_id, None
        while True:
            body = self._exec(self.service.users().history().list(
                userId="me", startHistoryId=start_history_id, historyTypes=["messageAdded"], labelId="INBOX",
                **({"pageToken": token} if token else {})))
            for h in body.get("history", []):
                for added in h.get("messagesAdded", []):
                    if (mid := added["message"]["id"]) not in ids:
                        ids.append(mid)
            cursor = str(body.get("historyId", cursor))
            token = body.get("nextPageToken")
            if not token:
                break
        out = []
        for mid in ids:
            e = to_inbound(self._exec(self.service.users().messages().get(userId="me", id=mid, format="full")))
            if e.sender != self.self_address and DEAL_SUBJECT.search(e.subject):
                out.append(e)
        return out, cursor
