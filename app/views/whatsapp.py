"""Meta WhatsApp Cloud API webhook.

GET  /webhooks/whatsapp  – verification handshake (hub.challenge)
POST /webhooks/whatsapp  – inbound messages + delivery statuses

Point your Meta app's webhook at ``https://<your-domain>/webhooks/whatsapp`` and
use ``WA_VERIFY_TOKEN`` as the verify token.
"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from ..extensions import csrf, db
from ..services.intent_router import handle_inbound
from ..services.whatsapp_client import (
    WhatsAppClient,
    get_or_create_conversation,
    log_inbound,
)
from ..models import WaMessage

log = logging.getLogger(__name__)
bp = Blueprint("whatsapp", __name__, url_prefix="/webhooks")

# Meta cannot send a CSRF token.
csrf.exempt(bp)


@bp.get("/whatsapp")
def verify():
    params = request.args
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == current_app.config["WA_VERIFY_TOKEN"]:
        log.info("WhatsApp webhook verified.")
        return challenge or "", 200
    log.warning("WhatsApp webhook verification failed (mode=%s).", mode)
    return jsonify({"error": "verification_failed"}), 403


@bp.post("/whatsapp")
def inbound():
    body = request.get_json(silent=True) or {}

    # Always return 200 quickly; Meta retries aggressively on non-2xx.
    try:
        _process(body)
    except Exception as exc:  # noqa: BLE001 - never 500 back at Meta
        log.exception("WhatsApp webhook error: %s", exc)
        db.session.rollback()
    return jsonify({"received": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
def _process(body: dict) -> None:
    if body.get("object") != "whatsapp_business_account":
        return

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}

            for status in value.get("statuses", []) or []:
                _handle_status(status)

            contacts = {c.get("wa_id"): c.get("profile", {}).get("name")
                        for c in value.get("contacts", []) or []}

            for message in value.get("messages", []) or []:
                _handle_message(message, contacts)

    db.session.commit()


def _handle_status(status: dict) -> None:
    wa_id = status.get("id")
    state = status.get("status")
    if not wa_id or not state:
        return
    row = WaMessage.query.filter_by(wa_message_id=wa_id).first()
    if row:
        row.status = state
        if state == "failed" and status.get("errors"):
            row.payload_json = (row.payload_json or "") + \
                f'\n{{"errors": {status["errors"]}}}'


def _handle_message(message: dict, contacts: dict) -> None:
    wa_id = message.get("from")
    if not wa_id:
        return

    msg_type = message.get("type")
    profile_name = contacts.get(wa_id)
    conversation = get_or_create_conversation(wa_id, profile_name=profile_name)

    text_body = None
    interactive_id = None
    media_url = None

    if msg_type == "text":
        text_body = (message.get("text") or {}).get("body", "")

    elif msg_type == "interactive":
        interactive = message.get("interactive") or {}
        itype = interactive.get("type")
        if itype == "button_reply":
            interactive_id = (interactive.get("button_reply") or {}).get("id")
            text_body = (interactive.get("button_reply") or {}).get("title")
        elif itype == "list_reply":
            interactive_id = (interactive.get("list_reply") or {}).get("id")
            text_body = (interactive.get("list_reply") or {}).get("title")

    elif msg_type in {"image", "document"}:
        media = message.get(msg_type) or {}
        media_id = media.get("id")
        media_url = _download_media(media_id, media.get("mime_type")) if media_id else None
        text_body = media.get("caption") or ""

    elif msg_type == "button":
        # Legacy quick-reply payload.
        interactive_id = (message.get("button") or {}).get("payload")
        text_body = (message.get("button") or {}).get("text")

    elif msg_type in {"audio", "video", "sticker", "location", "contacts"}:
        text_body = f"[{msg_type} received]"

    else:
        log.info("Unhandled WhatsApp message type: %s", msg_type)
        return

    log_inbound(
        conversation,
        body=text_body or "",
        msg_type=msg_type,
        payload={"id": interactive_id} if interactive_id else None,
        wa_message_id=message.get("id"),
        media_url=media_url,
    )

    if message.get("id"):
        try:
            WhatsAppClient().mark_read(message["id"])
        except Exception:  # noqa: BLE001 - non fatal
            pass

    replies = handle_inbound(
        conversation, text_body=text_body, interactive_id=interactive_id, media_url=media_url,
    )

    client = WhatsAppClient()
    for reply in replies:
        kind = reply.get("type")
        if kind == "buttons":
            client.send_buttons(wa_id, reply["body"], reply["buttons"],
                                header=reply.get("header"), conversation=conversation)
        elif kind == "list":
            client.send_list(wa_id, reply["body"], reply["button"], reply["sections"],
                             conversation=conversation)
        else:
            client.send_text(wa_id, reply["body"], conversation=conversation)


def _download_media(media_id: str, mime_type: str | None) -> str | None:
    """Fetch a media object from Meta and store it in the instance uploads folder.

    Returns a public-ish URL path that the job card can reference.
    """
    import requests

    cfg = current_app.config
    if cfg.get("WA_MODE") != "live" or not cfg.get("WA_ACCESS_TOKEN"):
        # Simulator: keep a placeholder so the inbox still shows the attachment.
        return f"/static/img/whatsapp-media-{media_id}.jpg"

    try:
        meta = requests.get(
            f"{cfg['WA_GRAPH_URL']}/{cfg['WA_API_VERSION']}/{media_id}",
            headers={"Authorization": f"Bearer {cfg['WA_ACCESS_TOKEN']}"},
            timeout=20,
        ).json()
        url = meta.get("url")
        if not url:
            return None
        blob = requests.get(
            url, headers={"Authorization": f"Bearer {cfg['WA_ACCESS_TOKEN']}"}, timeout=45
        )
        ext = (mime_type or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
        upload_dir = cfg["UPLOAD_DIR"]
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"wa_{media_id}.{ext}"
        (upload_dir / filename).write_bytes(blob.content)
        return f"/uploads/{filename}"
    except Exception as exc:  # noqa: BLE001
        log.error("Media download failed for %s: %s", media_id, exc)
        return None
