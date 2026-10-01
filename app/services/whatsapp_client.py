"""Thin wrapper around the WhatsApp Cloud API.

Two modes:

* ``simulator`` (default) – nothing leaves the machine. Outbound messages are
  persisted in the database and rendered in the built-in web inbox, so the whole
  bot can be demoed and tested without a Meta Business account.
* ``live`` – real HTTP calls to ``graph.facebook.com``.
"""
from __future__ import annotations

import logging
import re

import requests
from flask import current_app

from ..extensions import db
from ..models import WaConversation, WaMessage

log = logging.getLogger(__name__)


class WhatsAppError(Exception):
    pass


def normalise_msisdn(raw: str | None) -> str:
    """+263 77 555 0555 / 0775550555 -> 263775550555"""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "263" + digits[1:]
    return digits


class WhatsAppClient:
    """Send messages to a WhatsApp number, or simulate doing so."""

    def __init__(self, app=None):
        self.app = app or current_app

    # ── config helpers ───────────────────────────────────────────────────
    @property
    def cfg(self):
        return self.app.config

    @property
    def is_live(self) -> bool:
        return (
            self.cfg.get("WA_MODE") == "live"
            and bool(self.cfg.get("WA_ACCESS_TOKEN"))
            and bool(self.cfg.get("WA_PHONE_NUMBER_ID"))
        )

    @property
    def missing_live_settings(self) -> list[str]:
        """Which of the three settings is stopping a real send.

        Naming them is the whole point: the alternative is a deployment that
        accepts messages, computes replies and stores them as *delivered*, while
        the customer's phone stays silent and nothing anywhere says why.
        """
        return [
            name for name, present in (
                ("WA_MODE=live", self.cfg.get("WA_MODE") == "live"),
                ("WA_ACCESS_TOKEN", bool(self.cfg.get("WA_ACCESS_TOKEN"))),
                ("WA_PHONE_NUMBER_ID", bool(self.cfg.get("WA_PHONE_NUMBER_ID"))),
            ) if not present
        ]

    @property
    def _endpoint(self) -> str:
        return (
            f"{self.cfg['WA_GRAPH_URL']}/{self.cfg['WA_API_VERSION']}"
            f"/{self.cfg['WA_PHONE_NUMBER_ID']}/messages"
        )

    # ── transport ────────────────────────────────────────────────────────
    def _post(self, payload: dict) -> dict:
        if not self.is_live:
            # WARNING, not INFO, and it names what is missing. A silent no-op here
            # is indistinguishable from a working bot in the inbox, which is how a
            # production deployment sat in simulator mode answering nobody.
            #
            # It repeats for every message on purpose: while the deployment is
            # misconfigured each individual reply is being lost, and that is worth
            # seeing rather than once at boot where it scrolls away. It stops the
            # moment WA_MODE=live is set. The boot banner and the inbox banner
            # carry the same message for anyone who is not watching the log.
            log.warning(
                "WhatsApp is in SIMULATOR mode - nothing is being sent to "
                "WhatsApp. Missing: %s. Messages are still stored so the inbox "
                "shows the conversation.",
                ", ".join(self.missing_live_settings) or "(unknown)",
            )
            log.info("[WA-SIM] -> %s :: %s", payload.get("to"), payload)
            return {"simulated": True, "payload": payload}
        try:
            resp = requests.post(
                self._endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.cfg['WA_ACCESS_TOKEN']}",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )
        except requests.RequestException as exc:  # pragma: no cover - network
            raise WhatsAppError(str(exc)) from exc
        if resp.status_code >= 400:
            raise WhatsAppError(f"{resp.status_code}: {resp.text[:400]}")
        return resp.json()

    # ── public API ───────────────────────────────────────────────────────
    def send_text(self, to: str, body: str, *, conversation: WaConversation | None = None,
                  is_bot: bool = True, intent: str | None = None,
                  job_id: int | None = None) -> WaMessage | None:
        to = normalise_msisdn(to)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        return self._dispatch(to, payload, body, "text", conversation, is_bot, intent, job_id)

    def send_buttons(self, to: str, body: str, buttons: list[dict], *,
                     header: str | None = None, footer: str | None = None,
                     conversation: WaConversation | None = None, intent: str | None = None,
                     job_id: int | None = None) -> WaMessage | None:
        """buttons: [{"id": "menu_quote", "title": "Get a quote"}] (max 3)"""
        to = normalise_msisdn(to)
        interactive = {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                    for b in buttons[:3]
                ]
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return self._dispatch(to, payload, body, "interactive", conversation, True, intent, job_id,
                              extra={"buttons": buttons})

    def send_list(self, to: str, body: str, button_text: str, sections: list[dict], *,
                  header: str | None = None, footer: str | None = None,
                  conversation: WaConversation | None = None, intent: str | None = None,
                  job_id: int | None = None) -> WaMessage | None:
        """sections: [{"title": "Services", "rows": [{"id": ..., "title": ..., "description": ...}]}]"""
        to = normalise_msisdn(to)
        interactive = {
            "type": "list",
            "body": {"text": body},
            "action": {"button": button_text[:20], "sections": sections[:10]},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return self._dispatch(to, payload, body, "interactive", conversation, True, intent, job_id,
                              extra={"sections": sections})

    def send_flow(self, to: str, body: str, flow_id: str, *, flow_token: str = "enquiry",
                  cta: str = "Open form", header: str | None = None,
                  footer: str | None = None, screen: str = "ENQUIRY",
                  conversation: WaConversation | None = None,
                  intent: str | None = None, job_id: int | None = None) -> WaMessage | None:
        """Offer a WhatsApp Flow — a form that opens inside WhatsApp.

        The screen mode is ``draft``, which is what Meta requires while the Flow
        is still being built; publish it and switch this to ``published`` (or use
        a template's flow button, which is the only way to offer a form outside
        the 24-hour service window).

        ``flow_token`` is returned to us untouched in the ``nfm_reply``, so it is
        what routes the answers. Anything before a colon names the form.

        ``screen`` is the Flow's own first screen, by its API name. Each Flow has
        its own — the enquiry form opens ``ENQUIRY``, the booking form opens
        ``BOOKING`` — and it must match the builder exactly, because Meta rejects
        a screen name the Flow does not define.
        """
        to = normalise_msisdn(to)
        interactive = {
            "type": "flow",
            "body": {"text": body},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": flow_token,
                    "flow_id": flow_id,
                    "flow_cta": cta[:20],
                    "flow_action": "navigate",
                    "flow_action_payload": {"screen": screen},
                },
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return self._dispatch(to, payload, body, "interactive", conversation, True, intent,
                              job_id, extra={"flow_id": flow_id, "flow_token": flow_token})

    def send_document(self, to: str, link: str, filename: str, *, caption: str | None = None,
                      conversation: WaConversation | None = None, intent: str | None = None,
                      job_id: int | None = None) -> WaMessage | None:
        """Send a PDF (or other file) by public link.

        Meta fetches ``link`` itself, so it must be reachable from the internet
        over HTTPS. In simulator mode nothing leaves the machine.
        """
        to = normalise_msisdn(to)
        document = {"link": link, "filename": filename[:240]}
        if caption:
            document["caption"] = caption[:1000]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": document,
        }
        body = caption or f"[{filename}]"
        return self._dispatch(to, payload, body, "document", conversation, True,
                              intent, job_id, extra={"link": link, "filename": filename})

    def send_template(self, to: str, template: str, params: list[str], *,
                      lang: str = "en", conversation: WaConversation | None = None,
                      job_id: int | None = None,
                      document: dict | None = None) -> WaMessage | None:
        """Business-initiated message outside the 24h window must be a template.

        ``document`` attaches a **document header**, which is the only way to
        deliver a PDF outside the service window: Meta rejects a free-form
        document message there, so ``document_share`` is the approved template
        that carries one. The document must be publicly reachable, because Meta
        fetches it itself.
        """
        to = normalise_msisdn(to)
        components: list[dict] = []
        if document and document.get("link"):
            header: dict = {"link": document["link"]}
            if document.get("filename"):
                header["filename"] = document["filename"]
            components.append({"type": "header",
                               "parameters": [{"type": "document", "document": header}]})
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in params],
        })
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": lang},
                "components": components,
            },
        }
        body = f"[template:{template}] " + " | ".join(params)
        return self._dispatch(to, payload, body, "template", conversation, True,
                              f"template:{template}", job_id)

    def mark_read(self, wa_message_id: str) -> None:
        if not self.is_live:
            return
        try:
            self._post_read(wa_message_id)
        except WhatsAppError:  # pragma: no cover
            log.warning("Could not mark %s as read", wa_message_id)

    def _post_read(self, wa_message_id: str) -> None:  # pragma: no cover - live only
        requests.post(
            self._endpoint,
            json={"messaging_product": "whatsapp", "status": "read",
                  "message_id": wa_message_id},
            headers={"Authorization": f"Bearer {self.cfg['WA_ACCESS_TOKEN']}"},
            timeout=10,
        )

    # ── persistence ──────────────────────────────────────────────────────
    def _dispatch(self, to, payload, body, msg_type, conversation, is_bot, intent, job_id,
                  extra: dict | None = None) -> WaMessage | None:
        if not to:
            return None
        if conversation is None:
            conversation = get_or_create_conversation(to)

        status = "delivered"
        try:
            result = self._post(payload)
            if isinstance(result, dict) and result.get("simulated"):
                # Recording this as "delivered" is a lie the operator has no way to
                # see through: the inbox draws delivery ticks for messages that
                # never left the machine. ConnectLink never had this problem
                # because it always posts and always reports the status code.
                status = "simulated"
        except WhatsAppError as exc:
            status = "failed"
            log.error("WhatsApp send failed: %s", exc)

        message = log_outbound(
            conversation, body=body, msg_type=msg_type, payload=extra,
            is_bot=is_bot, intent=intent, job_id=job_id, status=status,
        )
        return message


# ─────────────────────────────────────────────────────────────────────────────
# Conversation / message persistence helpers (shared with the webhook)
# ─────────────────────────────────────────────────────────────────────────────
def get_or_create_conversation(wa_id: str, profile_name: str | None = None) -> WaConversation:
    wa_id = normalise_msisdn(wa_id)
    conversation = WaConversation.query.filter_by(wa_id=wa_id).first()
    if conversation:
        if profile_name and not conversation.profile_name:
            conversation.profile_name = profile_name
        return conversation

    conversation = WaConversation(wa_id=wa_id, profile_name=profile_name, state="MAIN_MENU")
    conversation.context = {}
    db.session.add(conversation)
    db.session.flush()

    # Auto-link to an existing customer record by phone number.
    from ..models import Customer

    customer = Customer.query.filter(
        db.or_(Customer.phone.like(f"%{wa_id[-9:]}%"), Customer.whatsapp.like(f"%{wa_id[-9:]}%"))
    ).first()
    if customer:
        conversation.customer_id = customer.id
    db.session.commit()
    return conversation


def log_inbound(conversation: WaConversation, *, body: str, msg_type: str = "text",
                payload: dict | None = None, wa_message_id: str | None = None,
                media_url: str | None = None) -> WaMessage:
    import json

    message = WaMessage(
        conversation_id=conversation.id,
        direction="inbound",
        msg_type=msg_type,
        body=body,
        payload_json=json.dumps(payload or {}),
        media_url=media_url,
        wa_message_id=wa_message_id,
        status="received",
        is_bot=False,
    )
    db.session.add(message)
    db.session.flush()  # populate created_at before copying it onto the thread
    conversation.unread = (conversation.unread or 0) + 1
    conversation.last_inbound_at = message.created_at
    conversation.last_message_at = message.created_at
    db.session.commit()
    return message


def log_outbound(conversation: WaConversation, *, body: str, msg_type: str = "text",
                 payload: dict | None = None, is_bot: bool = True, intent: str | None = None,
                 job_id: int | None = None, status: str = "delivered",
                 media_url: str | None = None) -> WaMessage:
    import json

    message = WaMessage(
        conversation_id=conversation.id,
        direction="outbound",
        msg_type=msg_type,
        body=body,
        payload_json=json.dumps(payload or {}),
        media_url=media_url,
        status=status,
        is_bot=is_bot,
        intent=intent,
        job_id=job_id,
    )
    db.session.add(message)
    db.session.flush()  # populate created_at before copying it onto the thread
    conversation.last_outbound_at = message.created_at
    conversation.last_message_at = message.created_at
    if conversation.unread:
        conversation.unread = 0
    db.session.commit()
    return message
