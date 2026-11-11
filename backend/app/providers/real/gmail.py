"""Gmail provider: read new deal emails via history.list, reply in-thread. docs/reference/gmail-api.md."""

import base64
import html
import re
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from app.tools.definitions import GmailReplyIn, GmailReplyOut, InboundEmail
from app.tools.registry import AmbiguousToolError, PermanentToolError, TransientToolError

DEAL_SUBJECT = re.compile(r"closed[\s-]*won|new deal|deal closed", re.I)
AUTO_SUBJECT = re.compile(r"^(automatic reply|auto[- ]?reply|out of (the )?office|undeliverable|delivery status)", re.I)
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
    text = "\n".join(rich)
    # Keep link targets (e.g. a Salesforce link behind the word "Opportunity"): <a href="u">t</a> -> "t (u)".
    text = re.sub(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", r"\2 (\1)", text, flags=re.I | re.S)
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


TRUSTED_AUTHSERV_ID = "mx.google.com"


def _aligned(signing_domain: str, from_domain: str) -> bool:
    return bool(signing_domain) and (from_domain == signing_domain or from_domain.endswith("." + signing_domain))


def sender_auth(auth_results: list[str], sender: str) -> tuple[bool, str]:
    """Gmail's verdict on the From address, from `Authentication-Results` header values (top-most first).

    Only headers stamped by Gmail's own MTA (authserv-id mx.google.com) count; anything else could have
    been written by the sender. Gmail prepends its header, so the top-most trusted one wins over any
    copy injected further down. Authenticated = dmarc=pass for the From domain, or dkim=pass with a
    signing domain equal to the From domain or a parent of it. SPF alone is never enough (it checks the
    envelope sender, not From).
    """
    from_domain = sender.rpartition("@")[2].strip().lower()
    if not from_domain:
        return False, "no From address"
    for value in auth_results:
        clean = re.sub(r"\([^()]*\)", " ", value)  # drop RFC 5322 comments
        authserv, *results = [p.strip() for p in clean.split(";")]
        if (authserv.split() or [""])[0].lower() != TRUSTED_AUTHSERV_ID:
            continue
        verdicts = []
        for r in results:
            tokens = r.split()
            if not tokens or "=" not in tokens[0]:
                continue
            method, _, result = tokens[0].lower().partition("=")
            props = {k: v for k, _, v in (t.lower().partition("=") for t in tokens[1:])}
            verdicts.append(f"{method}={result}")
            if result != "pass":
                continue
            if method == "dmarc" and props.get("header.from") == from_domain:
                return True, f"dmarc=pass header.from={from_domain}"
            signer = props.get("header.d") or props.get("header.i", "").rpartition("@")[2]
            if method == "dkim" and _aligned(signer, from_domain):
                return True, f"dkim=pass d={signer} aligned with {from_domain}"
        return False, f"not authenticated for {from_domain or '?'}: {' '.join(verdicts) or 'no results'}"[:200]
    return False, f"no Authentication-Results from {TRUSTED_AUTHSERV_ID}"


def to_inbound(msg: dict) -> InboundEmail:
    h = _headers(msg)
    sender = parseaddr(h.get("from", ""))[1].lower()
    authenticated, auth_detail = sender_auth(
        [x["value"] for x in msg.get("payload", {}).get("headers", []) if x["name"].lower() == "authentication-results"],
        sender)
    subject = h.get("subject", "")
    auto = (h.get("auto-submitted", "no").lower() != "no" or "x-autoreply" in h or "x-autorespond" in h
            or h.get("precedence", "").lower() in ("bulk", "auto_reply", "junk") or bool(AUTO_SUBJECT.search(subject)))
    return InboundEmail(message_id=msg["id"], thread_id=msg["threadId"],
                        rfc_message_id=h.get("message-id", ""), sender=sender,
                        subject=subject, body=extract_text(msg.get("payload", {})), auto_reply=auto,
                        sender_authenticated=authenticated, auth_detail=auth_detail)


def build_reply(a: GmailReplyIn, sender: str) -> dict:
    m = EmailMessage()
    m["To"], m["From"], m["Subject"] = a.to, sender, a.subject
    if a.rfc_message_id:
        m["In-Reply-To"] = m["References"] = a.rfc_message_id
    if a.message_id_header:
        m["Message-ID"] = a.message_id_header  # deterministic: lets find_sent prove whether it went out
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

    def _exec(self, req, write: bool = False) -> dict:
        try:
            return req.execute()
        except Exception as e:  # googleapiclient.errors.HttpError and transport errors
            status = getattr(getattr(e, "resp", None), "status", 0)
            if write and (status == 0 or status >= 500):
                # Timeout / reset / gateway error after the request went out: Gmail may have sent it.
                raise AmbiguousToolError(f"gmail {status}: {e}") from e
            if status == 404:
                raise PermanentToolError("not_found") from e
            if status in (0, 429) or status >= 500 or (status == 403 and "rateLimitExceeded" in str(e)):
                raise TransientToolError(f"gmail {status}: {e}") from e
            raise PermanentToolError(f"gmail {status}: {e}") from e

    def reply(self, a: GmailReplyIn) -> GmailReplyOut:
        sent = self._exec(self.service.users().messages().send(userId="me", body=build_reply(a, self.self_address)),
                          write=True)
        return GmailReplyOut(message_id=sent["id"])

    def find_sent(self, message_id_header: str) -> str | None:
        """Gmail id of our sent message with this RFC 822 Message-ID, else None."""
        body = self._exec(self.service.users().messages().list(
            userId="me", q=f"in:sent rfc822msgid:{message_id_header.strip('<>')}", maxResults=1))
        msgs = body.get("messages") or []
        return msgs[0]["id"] if msgs else None

    def current_history_id(self) -> str:
        return str(self._exec(self.service.users().getProfile(userId="me"))["historyId"])

    def watch(self, topic: str) -> dict:
        return self._exec(self.service.users().watch(
            userId="me", body={"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"}))

    def new_deal_emails(self, start_history_id: str, is_relevant: Callable[[InboundEmail], bool] | None = None
                        ) -> tuple[list[InboundEmail], str]:
        """Messages added to INBOX since the cursor, excluding our own mail and filtered by `is_relevant`
        (default: deal-like subject). Returns (emails, new cursor)."""
        is_relevant = is_relevant or (lambda e: bool(DEAL_SUBJECT.search(e.subject)))
        ids: list[str] = []
        cursor, token = start_history_id, None
        while True:
            try:
                body = self._exec(self.service.users().history().list(
                    userId="me", startHistoryId=start_history_id, historyTypes=["messageAdded"], labelId="INBOX",
                    **({"pageToken": token} if token else {})))
            except PermanentToolError as e:
                if str(e) == "not_found":
                    raise PermanentToolError("history_too_old") from e
                raise
            for h in body.get("history", []):
                for added in h.get("messagesAdded", []):
                    if (mid := added["message"]["id"]) not in ids:
                        ids.append(mid)
            cursor = str(body.get("historyId", cursor))
            token = body.get("nextPageToken")
            if not token:
                break
        return self._fetch(ids, is_relevant), cursor

    def recent_deal_emails(self, is_relevant: Callable[[InboundEmail], bool]) -> list[InboundEmail]:
        """Full-sync fallback when the history cursor is too old: inbox mail from the last 2 days."""
        body = self._exec(self.service.users().messages().list(userId="me", q="in:inbox newer_than:2d",
                                                                maxResults=100))
        return self._fetch([m["id"] for m in body.get("messages", [])], is_relevant)

    def _fetch(self, ids: list[str], is_relevant: Callable[[InboundEmail], bool]) -> list[InboundEmail]:
        out = []
        for mid in ids:
            try:
                msg = self._exec(self.service.users().messages().get(userId="me", id=mid, format="full"))
            except PermanentToolError as e:
                if str(e) == "not_found":
                    continue  # deleted/moved between history.list and get: skip it, keep the rest
                raise
            e = to_inbound(msg)
            if e.sender != self.self_address and not e.auto_reply and is_relevant(e):
                out.append(e)
        return out
