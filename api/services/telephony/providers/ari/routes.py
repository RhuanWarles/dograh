"""Authenticated diagnostics for a tenant's managed SIP trunk."""

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user_with_selected_organization

from .sip_status import SIPTrunkStatus, read_registration_status

router = APIRouter()


@router.get("/ari/sip-trunks/{config_id}/status", response_model=SIPTrunkStatus)
async def get_sip_trunk_status(
    config_id: int, user: UserModel = Depends(get_user_with_selected_organization)
):
    row = await db_client.get_telephony_configuration_for_org(
        config_id, user.selected_organization_id
    )
    if (
        row is None
        or row.provider != "ari"
        or (row.credentials or {}).get("connection_mode") != "sip_trunk"
    ):
        raise HTTPException(404, "SIP trunk not found")
    if row.inactive:
        return SIPTrunkStatus(
            status="disabled",
            message="This configuration is inactive. Correct its settings and reactivate it.",
        )
    return read_registration_status(row.credentials)
