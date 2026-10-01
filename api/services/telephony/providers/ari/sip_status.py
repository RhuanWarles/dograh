"""Read the gateway's registration snapshot without exposing credentials."""

import json
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class SIPTrunkStatus(BaseModel):
    status: Literal[
        "registered",
        "unregistered",
        "rejected",
        "stopped",
        "pending",
        "unavailable",
        "disabled",
    ]
    message: str
    contact_user: str | None = None


def read_registration_status(credentials: dict) -> SIPTrunkStatus:
    contact = credentials.get("stasis_app_name")
    if not credentials.get("sip", {}).get("registration_enabled", True):
        return SIPTrunkStatus(
            status="disabled",
            message="SIP REGISTER is disabled. Configure your carrier to send incoming calls to the contact user below.",
            contact_user=contact,
        )
    directory = os.environ.get("SIP_GATEWAY_CONFIG_DIR")
    try:
        if not directory:
            raise OSError("Gateway not configured")
        snapshot = json.loads((Path(directory) / "status.json").read_text())
        if time.time() - snapshot["updated_at"] > 30:
            raise OSError("Stale gateway status")
    except (OSError, ValueError, KeyError, TypeError):
        return SIPTrunkStatus(
            status="unavailable",
            message="The SIP gateway is unavailable. Check the gateway service.",
        )
    state = snapshot.get("registrations", {}).get(contact, "pending")
    messages = {
        "registered": "Registered with the carrier. Assign phone numbers and an inbound agent to receive calls.",
        "unregistered": "Not registered yet. Check the server, port, transport and network access.",
        "rejected": "The carrier rejected registration. Check the username, password and SIP domain.",
        "stopped": "Registration stopped. Check the carrier credentials and restrictions.",
        "pending": "Waiting for synchronization. New or updated trunks are applied within 60 seconds.",
    }
    if state not in messages:
        state = "pending"
    return SIPTrunkStatus(status=state, message=messages[state], contact_user=contact)
