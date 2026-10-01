"""Meta WhatsApp Cloud API webhook.

GET  /webhooks/whatsapp  – verification handshake (hub.challenge)
POST /webhooks/whatsapp  – inbound messages + delivery statuses

``/webhook`` is an accepted alias serving the same two endpoints (see the bottom
of this module), so either URL can be pasted into the Meta app dashboard.

Point your Meta app's webhook at ``https://<your-domain>/webhooks/whatsapp`` and
use ``WA_VERIFY_TOKEN`` as the verify token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
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
    # Read the raw body before parsing: the signature is computed over the exact
    # bytes Meta sent, so re-serialising the JSON would break the comparison.
    raw = request.get_data()
    if not _webhook_token_ok():
        log.warning("Rejected WhatsApp webhook: bad or missing webhook token.")
        return jsonify({"error": "invalid_webhook_token"}), 403
    if not _signature_ok(raw):
        log.warning("Rejected WhatsApp webhook: missing or invalid X-Hub-Signature-256.")
        return jsonify({"error": "invalid_signature"}), 403

    body = request.get_json(silent=True) or {}

    # Always return 200 quickly; Meta retries aggressively on non-2xx.
    summary = {}
    try:
        summary = _process(body)
    except Exception as exc:  # noqa: BLE001 - never 500 back at Meta
        log.exception("WhatsApp webhook error: %s", exc)
        db.session.rollback()
    return jsonify({"received": True, **summary}), 200


# ── /webhook alias ───────────────────────────────────────────────────────────
# Meta accepts whatever URL you paste into the app dashboard, and "/webhook" is
# the obvious thing to type. The canonical route is /webhooks/whatsapp; this
# alias serves the identical two endpoints so the short URL also verifies and
# receives. Without it, verification fails with a 404.
#
# strict_slashes=False so both "/webhook" and "/webhook/" work: Meta does not
# follow the 308 redirect on the POST, so the body would be lost.
alias = Blueprint("whatsapp_alias", __name__)
csrf.exempt(alias)


@alias.get("/webhook", strict_slashes=False)
def verify_alias():
    return verify()


@alias.post("/webhook", strict_slashes=False)
def inbound_alias():
    return inbound()


def _webhook_token_ok() -> bool:
    """Optional second lock on the webhook delivery URL.

    ``hub.verify_token`` only answers *who configured the webhook* — it rides in
    the GET handshake and never touches a POST body, so on its own it protects
    nothing about the messages Meta delivers. Setting ``WA_WEBHOOK_TOKEN``
    reuses that same shared secret as a query check on every delivery, which
    turns "anyone who knows our domain" into "anyone who knows the exact URL we
    pasted into Meta".

    Deliberately opt-in: with the config empty, behaviour is unchanged. This is
    **not** a substitute for ``WA_APP_SECRET`` — it proves the caller knows the
    URL, not that Meta sent the payload, so a leaked URL is still a forged-message
    hole. Set the app secret when you can.
    """
    expected = current_app.config.get("WA_WEBHOOK_TOKEN") or ""
    if not expected:
        return True
    supplied = (request.args.get("token")
                or request.headers.get("X-Webhook-Token")
                or "")
    return hmac.compare_digest(supplied, expected)


def _signature_ok(raw: bytes) -> bool:
    """Verify Meta's ``X-Hub-Signature-256`` header.

    Without this the webhook URL is an open door: anyone who learns it can POST a
    hand-written payload and have the bot create bookings, customers and job
    cards on their behalf. Only enforced when ``WA_APP_SECRET`` is configured, so
    a simulator install with no Meta app still works.
    """
    secret = current_app.config.get("WA_APP_SECRET") or ""
    if not secret:
        return True                     # not configured — nothing to verify against

    header = request.headers.get("X-Hub-Signature-256", "")
    if not header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


# ─────────────────────────────────────────────────────────────────────────────
def _process(body: dict) -> dict:
    """Handle one webhook body. Returns a small summary for the response."""
    summary = {"duplicates": 0}
    if body.get("object") != "whatsapp_business_account":
        return summary

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}

            for status in value.get("statuses", []) or []:
                _handle_status(status)

            contacts = {c.get("wa_id"): c.get("profile", {}).get("name")
                        for c in value.get("contacts", []) or []}

            for message in value.get("messages", []) or []:
                if _handle_message(message, contacts) == "duplicate":
                    summary["duplicates"] += 1

    db.session.commit()
    return summary


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


def _flow_media(answers: dict) -> list[dict]:
    """Media the Flow itself collected, from a PhotoPicker or DocumentPicker.

    Meta returns these under the **component's own ``name``** — whatever the Flow
    builder called it, so ``attachment`` or ``photo_picker`` or anything else —
    as a list of ``{file_name, mime_type, sha256, id}``. Since the key is the
    customer's to choose, this looks for the *shape* rather than hard-coding one,
    and a form with no picker simply yields nothing.

    Each entry is then downloaded through the same ``_download_media`` an inbound
    chat image or document uses, so the two routes store the same kind of thing
    and the desk sees one sort of attachment either way.
    """
    found: list[dict] = []
    for value in (answers or {}).values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("id"):
                found.append({
                    "id": str(item["id"]),
                    "name": str(item.get("file_name") or "")[:120],
                    "mime": item.get("mime_type"),
                })
    return found


def _flow_summary(data: dict, limit: int = 300) -> str:
    """A one-line digest of a submitted Flow, for the inbox and the thread.

    The message row stores the body a person reads, so putting the answers here
    means an enquiry can be understood from the conversation itself rather than
    only by opening the booking record. Field names are trimmed to something
    readable ("reg no: ADZ4477") because the desk sees this, not the developer.
    """
    parts = []
    for key, value in (data or {}).items():
        if isinstance(value, (list, tuple)):
            # A picker's entries are dicts; showing them raw would put a wall of
            # JSON in the thread. The filenames are the bit worth reading.
            names = [str(item.get("file_name") or item.get("id"))
                     for item in value if isinstance(item, dict)]
            value = ", ".join(names) if names else ", ".join(str(item) for item in value)
        if value is None:
            continue
        shown = str(value).strip()
        if not shown:
            continue
        parts.append(f"{str(key).replace('_', ' ')}: {shown}")
    return "; ".join(parts)[:limit]


def _handle_message(message: dict, contacts: dict) -> str | None:
    wa_id = message.get("from")
    if not wa_id:
        return None

    msg_type = message.get("type")
    profile_name = contacts.get(wa_id)
    conversation = get_or_create_conversation(wa_id, profile_name=profile_name)

    # Meta redelivers a webhook whenever we do not acknowledge it fast enough.
    # Handling the same message twice double-books, double-replies and
    # double-notifies, so the message id is claimed before any work happens.
    wa_message_id = message.get("id")
    if wa_message_id and WaMessage.query.filter_by(wa_message_id=wa_message_id).first():
        log.info("Ignoring redelivered WhatsApp message %s", wa_message_id)
        return "duplicate"

    text_body = None
    interactive_id = None
    media_url = None
    media_name = None
    flow_response = None

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
        elif itype == "nfm_reply":
            # A completed Flow. Meta sends the answers as a JSON *string* inside
            # response_json, not as an object, and ``flow_token`` is whatever we
            # set when the form was sent — that token is what routes it.
            reply = interactive.get("nfm_reply") or {}
            raw = reply.get("response_json") or ""
            try:
                answers = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                log.warning("Flow response was not valid JSON: %r", str(raw)[:200])
                answers = {}
            if not isinstance(answers, dict):
                answers = {}
            flow_response = {"flow_token": reply.get("flow_token"), "data": answers}
            # A Flow's own picker media. Downloaded here, where the network calls
            # already live, and handed to the router to attach.
            media = []
            for entry in _flow_media(answers):
                url = _download_media(entry["id"], entry["mime"])
                if url:
                    media.append({"url": url, "name": entry["name"]})
            if media:
                flow_response["media"] = media
            # The row keeps the body the desk reads, so the answers go here too —
            # an enquiry should be understandable from the thread alone.
            text_body = _flow_summary(answers) or "[Form submitted]"

    elif msg_type in {"image", "document"}:
        media = message.get(msg_type) or {}
        media_id = media.get("id")
        media_url = _download_media(media_id, media.get("mime_type")) if media_id else None
        # Meta names documents ("assessor report.pdf") but not images. For a PDF it
        # is the only clue the desk has about what the attachment is, so it is
        # carried through rather than thrown away.
        media_name = media.get("filename")
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
        wa_message_id=wa_message_id,
        media_url=media_url,
    )

    if wa_message_id:
        try:
            WhatsAppClient().mark_read(wa_message_id)
        except Exception:  # noqa: BLE001 - non fatal
            pass

    replies = handle_inbound(
        conversation, text_body=text_body, interactive_id=interactive_id, media_url=media_url,
        media_name=media_name, flow_response=flow_response,
    )

    client = WhatsAppClient()
    for reply in replies:
        kind = reply.get("type")
        if kind == "buttons":
            client.send_buttons(wa_id, reply["body"], reply["buttons"],
                                header=reply.get("header"), conversation=conversation)
        elif kind == "list":
            client.send_list(wa_id, reply["body"], reply["button"], reply["sections"],
                             header=reply.get("header"), footer=reply.get("footer"),
                             conversation=conversation)
        elif kind == "document":
            # A document the customer asked for mid-conversation, such as the
            # Download button on a quotation or receipt. Meta fetches the link
            # itself, so it has to be publicly reachable — which is what
            # public_url() guarantees when the router builds it.
            client.send_document(wa_id, reply["link"], reply["filename"],
                                 caption=reply.get("caption"), conversation=conversation)
        elif kind == "flow":
            # A WhatsApp Flow: a form that opens inside WhatsApp. The answers come
            # back as an ``nfm_reply``, handled above. Each Flow opens on its own
            # first screen — Meta rejects a screen name the Flow does not define,
            # so the router names it rather than this loop assuming one.
            client.send_flow(wa_id, reply["body"], reply["flow_id"],
                             flow_token=reply.get("flow_token") or "enquiry",
                             header=reply.get("header"), footer=reply.get("footer"),
                             screen=reply.get("screen") or "ENQUIRY",
                             conversation=conversation)
        else:
            client.send_text(wa_id, reply["body"], conversation=conversation)


# Meta mime types we accept as attachments, mapped to the extension we store. A
# PDF is the common case for an assessor's report; anything unmapped falls back to
# the mime subtype, sanitised, so a type we have never seen cannot write a
# filename with a slash or a space in it.
MEDIA_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
}


def _media_extension(mime_type: str | None) -> str:
    import re as _re

    mapped = MEDIA_EXTENSIONS.get((mime_type or "").lower())
    if mapped:
        return mapped
    subtype = (mime_type or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
    return _re.sub(r"[^a-z0-9]", "", subtype.lower())[:5] or "bin"


def _download_media(media_id: str, mime_type: str | None) -> str | None:
    """Fetch a media object from Meta and store it in the instance uploads folder.

    Returns a public-ish URL path that the job card can reference.
    """
    import requests

    cfg = current_app.config
    ext = _media_extension(mime_type)
    if cfg.get("WA_MODE") != "live" or not cfg.get("WA_ACCESS_TOKEN"):
        # Simulator: keep a placeholder so the inbox still shows the attachment.
        # It honours the extension so a simulated PDF is not drawn as an image.
        return f"/static/img/whatsapp-media-{media_id}.{ext}"

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
        ext = _media_extension(mime_type)
        upload_dir = cfg["UPLOAD_DIR"]
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"wa_{media_id}.{ext}"
        (upload_dir / filename).write_bytes(blob.content)
        return f"/uploads/{filename}"
    except Exception as exc:  # noqa: BLE001
        log.error("Media download failed for %s: %s", media_id, exc)
        return None
