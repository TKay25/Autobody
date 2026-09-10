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
    def _endpoint(self) -> str:
        return (
            f"{self.cfg['WA_GRAPH_URL']}/{self.cfg['WA_API_VERSION']}"
            f"/{self.cfg['WA_PHONE_NUMBER_ID']}/messages"
        )

    # ── transport ────────────────────────────────────────────────────────
    def _post(self, payload: dict) -> dict:
        if not self.is_live:
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
                      job_id: int | None = None) -> WaMessage | None:
        """Business-initiated message outside the 24h window must be a template."""
        to = normalise_msisdn(to)
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": lang},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
                ],
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
            self._post(payload)
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
