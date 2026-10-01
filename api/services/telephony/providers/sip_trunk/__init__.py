"""SIP Trunk telephony provider package."""

import os
import secrets
from typing import Any, Dict

from fastapi import HTTPException

from api.services.telephony.registry import (
    ConfigurationSetupState,
    ProviderSetupChecklist,
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    SetupStep,
    register,
)

from api.services.telephony.providers.ari.provider import ARIProvider
from api.services.telephony.providers.ari.transport import create_transport
from api.services.telephony.providers.ari.sip_gateway import gateway_enabled

from .config import SIPTrunkConfigurationRequest


_STASIS_APP_NAME_PREFIX = "dograh_"


def _generate_stasis_app_name() -> str:
    return f"{_STASIS_APP_NAME_PREFIX}{secrets.token_hex(6)}"


async def _preprocess_credentials_on_save(
    credentials: Dict[str, Any],
    existing_credentials: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    credentials = dict(credentials)

    if not gateway_enabled():
        raise HTTPException(
            503,
            "Enable the SIP gateway on this installation before adding a SIP trunk",
        )

    credentials["managed_sip"] = True

    if existing_credentials is None:
        credentials["stasis_app_name"] = _generate_stasis_app_name()
    elif existing_credentials.get("stasis_app_name"):
        credentials["stasis_app_name"] = existing_credentials["stasis_app_name"]

    return credentials


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    name = value.get("stasis_app_name", "")

    return {
        "provider": "sip_trunk",
        "managed_sip": True,
        "ari_endpoint": os.environ.get("SIP_GATEWAY_ENDPOINT", ""),
        "app_name": "dograh",
        "app_password": os.environ.get("SIP_GATEWAY_PASSWORD", ""),
        "stasis_app_name": name,
        "dial_string_template": f"PJSIP/{{number}}@{name}",
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="SIP Trunk",
    docs_url="https://docs.dograh.com/integrations/telephony/sip-trunk",
    fields=[
        ProviderUIField(
            name="sip.host",
            label="SIP server",
            type="text",
            placeholder="sip.your-carrier.com",
        ),
        ProviderUIField(
            name="sip.port",
            label="Port",
            type="number",
            default_value=5060,
        ),
        ProviderUIField(
            name="sip.transport",
            label="Transport",
            type="select",
            default_value="udp",
            options=[
                ProviderUIOption(value=v, label=v.upper())
                for v in ("udp", "tcp", "tls")
            ],
        ),
        ProviderUIField(
            name="sip.username",
            label="SIP username",
            type="text",
        ),
        ProviderUIField(
            name="sip.password",
            label="SIP password",
            type="password",
            sensitive=True,
        ),
        ProviderUIField(
            name="sip.auth_username",
            label="Authentication username",
            type="text",
            required=False,
            description="Leave empty to use the SIP username.",
        ),
        ProviderUIField(
            name="sip.registration_enabled",
            label="Register with carrier",
            type="boolean",
            default_value=True,
        ),
        ProviderUIField(
            name="sip.registrar",
            label="Registration server",
            type="text",
            required=False,
            description="Leave empty to use the SIP server.",
        ),
        ProviderUIField(
            name="sip.from_domain",
            label="SIP domain",
            type="text",
            required=False,
            description="Leave empty to use the SIP server.",
        ),
        ProviderUIField(
            name="sip.inbound_networks",
            label="Carrier inbound IPs / networks",
            type="text",
            required=False,
            description="Comma-separated IPs or CIDRs.",
        ),
    ],
)


def _setup_checklist(
    credentials: dict,
    state: ConfigurationSetupState,
) -> ProviderSetupChecklist:
    return ProviderSetupChecklist.from_steps(
        [
            SetupStep(
                key="phone-numbers",
                title="Add your carrier numbers",
                complete=state.active_phone_number_count > 0,
                description="Add a phone number or extension authorized by this carrier.",
                blocks_outbound=True,
            ),
            SetupStep(
                key="inbound-workflow",
                title="Assign an inbound agent",
                complete=state.inbound_routed_phone_number_count > 0,
                description="Choose an inbound workflow for numbers that receive calls.",
            ),
        ]
    )


SPEC = ProviderSpec(
    name="sip_trunk",
    provider_cls=ARIProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=SIPTrunkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    connectivity="sip",
    requires_caller_id=False,
    setup_checklist_resolver=_setup_checklist,
    requires_e164_destinations=True,
    preprocess_credentials_on_save=_preprocess_credentials_on_save,
)


register(SPEC)


__all__ = [
    "SPEC",
    "SIPTrunkConfigurationRequest",
]