"""Who is allowed to put messages on the workshop's own WhatsApp number.

`POST /api/whatsapp/simulate` drives the bot by hand. With WA_MODE=live it sends
from the business's real number to whatever number it is handed, which is why it
is not open to every login: Meta bans numbers used for unsolicited messaging.
"""
from __future__ import annotations


def test_the_manual_bot_driver_is_manager_only(app):
    """A front-desk or workshop login must not be able to send as the business."""
    client = app.test_client()
    login = client.post("/auth/login", json={
        "email": "estimator@topclass.co.zw", "password": "topclass123",
    })
    assert login.status_code == 200, login.get_data(as_text=True)

    res = client.post("/api/whatsapp/simulate", json={"body": "hello"})
    assert res.status_code == 403, res.get_data(as_text=True)


def test_a_manager_can_still_drive_the_bot(auth_client):
    """The owner is a manager, and the bot's own tests rely on this path."""
    res = auth_client.post("/api/whatsapp/simulate", json={"body": "hello"})
    assert res.status_code == 200, res.get_data(as_text=True)


def test_it_still_requires_a_login(client):
    assert client.post("/api/whatsapp/simulate", json={"body": "hello"}).status_code == 401
