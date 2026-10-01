"""Managed trunks must isolate tenants, protect credentials and render safe SIP config."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.routes.organization import _credentials_for_display, preserve_masked_fields
from api.services.telephony.providers.ari import (
    _config_loader,
    _preprocess_credentials_on_save,
)
from api.services.telephony.providers.ari.config import ARIConfigurationRequest
from api.services.telephony.providers.ari.provider import ARIProvider
from api.services.telephony.providers.ari.routes import get_sip_trunk_status
from api.services.telephony.providers.ari.sip_config import SIPTrunkConfiguration
from api.services.telephony.providers.ari.sip_gateway import (
    SIPGatewayReconciler,
    render_account,
)

SIP = {"host": "sip.example.com", "username": "account-1", "password": "private-secret"}
NAME = "dograh_abcdef123456"


def credentials(**changes):
    return {
        "connection_mode": "sip_trunk",
        "stasis_app_name": NAME,
        "sip": dict(SIP),
        **changes,
    }


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("SIP_GATEWAY_ENDPOINT", "http://sip-gateway:8088")
    monkeypatch.setenv("SIP_GATEWAY_PASSWORD", "deployment-admin-secret")
    monkeypatch.setenv("SIP_GATEWAY_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "sip.example.com\n[attack]"),
        ("host", "sip://host:5060"),
        ("username", "user\npassword=bad"),
        ("password", "secret\n[attack]"),
        ("port", 0),
        ("port", 65536),
        ("inbound_networks", "0.0.0.0/0"),
    ],
)
def test_rejects_invalid_or_injected_settings(field, value):
    with pytest.raises(ValidationError):
        SIPTrunkConfiguration.model_validate({**SIP, field: value})


def test_ari_remains_backwards_compatible_and_sip_requires_credentials():
    external = ARIConfigurationRequest(
        ari_endpoint="http://pbx:8088", app_name="user", app_password="secret"
    )
    assert external.connection_mode == "external_ari"
    with pytest.raises(ValidationError):
        ARIConfigurationRequest(connection_mode="sip_trunk")
    assert ARIConfigurationRequest(
        connection_mode="sip_trunk", sip=SIP
    ).sip.registration_enabled


@pytest.mark.asyncio
async def test_gateway_admin_credentials_are_never_persisted_or_returned(gateway):
    created = await _preprocess_credentials_on_save(
        {**credentials(), "app_password": "injected", "ari_endpoint": "http://evil"}
    )
    assert "app_password" not in created and "ari_endpoint" not in created
    display = _credentials_for_display("ari", created)
    assert display["sip"]["password"] != SIP["password"]
    assert "deployment-admin-secret" not in str(display)
    assert _config_loader(created)["app_password"] == "deployment-admin-secret"
    preserve_masked_fields("ari", display, created)
    assert display["sip"]["password"] == SIP["password"]


@pytest.mark.asyncio
async def test_unconfigured_gateway_does_not_accept_an_unusable_trunk(monkeypatch):
    monkeypatch.delenv("SIP_GATEWAY_ENDPOINT", raising=False)
    with pytest.raises(HTTPException) as error:
        await _preprocess_credentials_on_save(credentials())
    assert error.value.status_code == 503


def test_accounts_have_separate_routes_and_source_acls():
    first, first_routes = render_account(
        credentials(), ["+5511999990001"], ["203.0.113.8"]
    )
    second, second_routes = render_account(
        credentials(stasis_app_name="dograh_abcdef123457"),
        ["+5511999990002"],
        ["203.0.113.8"],
    )
    assert "line=yes" in first and f"endpoint={NAME}" in first
    assert "deny=0.0.0.0/0" in first and "permit=203.0.113.8" in first
    assert "match=203.0.113.8" not in first  # shared carrier IP cannot select a tenant
    assert "+5511999990002" not in first_routes
    assert "+5511999990001" not in second_routes
    assert f"endpoint={NAME}\n" not in second
    assert f"Goto({NAME}-numbers,+5511999990001,1)" in first_routes


def test_config_comments_cannot_truncate_password_and_numbers_cannot_inject_dialplan():
    config, routes = render_account(
        credentials(sip={**SIP, "password": "pass;word"}),
        ["+5511999990001\n[attack]"],
        ["203.0.113.8"],
    )
    assert r"password=pass\;word" in config
    assert "[attack]" not in routes
    assert "Stasis(" not in routes


@pytest.mark.asyncio
async def test_managed_origination_cannot_select_another_trunk_or_caller_id(gateway):
    provider = ARIProvider(
        {**_config_loader(credentials()), "from_numbers": ["+5511999990001"]}
    )
    for target, caller in [("PJSIP/123@other-trunk", None), ("123", "+5511999990002")]:
        with pytest.raises(ValueError):
            await provider.initiate_call(target, "", from_number=caller)


@pytest.mark.asyncio
async def test_status_is_scoped_to_the_selected_organization(monkeypatch):
    from api.services.telephony.providers.ari import routes

    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.db_client, "get_telephony_configuration_for_org", lookup)
    with pytest.raises(HTTPException) as error:
        await get_sip_trunk_status(12, SimpleNamespace(selected_organization_id=7))
    assert error.value.status_code == 404
    lookup.assert_awaited_once_with(12, 7)


@pytest.mark.asyncio
async def test_reconcile_removes_deleted_accounts_and_retries_failed_reload(
    gateway, monkeypatch
):
    from api.services.telephony.providers.ari import sip_gateway

    response = SimpleNamespace(status=204)
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.put.return_value = request
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(sip_gateway.aiohttp, "ClientSession", lambda **kwargs: session)
    db = SimpleNamespace(
        list_phone_numbers_for_config=AsyncMock(
            return_value=[
                SimpleNamespace(
                    address="+55 (11) 99999-0001",
                    address_normalized="+5511999990001",
                    is_active=True,
                    inbound_workflow_id=2,
                )
            ]
        )
    )
    row = SimpleNamespace(
        id=1, credentials=credentials(sip={**SIP, "inbound_networks": "203.0.113.8"})
    )
    reconciler = SIPGatewayReconciler()
    await reconciler.reconcile([row], db)
    assert NAME in (gateway / "pjsip.conf").read_text()
    assert (
        "exten => +5511999990001,1,Stasis(" in (gateway / "extensions.conf").read_text()
    )
    assert (gateway / "pjsip.conf").stat().st_mode & 0o777 == 0o600
    response.status = 503
    with pytest.raises(RuntimeError):
        await reconciler.reconcile([], db)
    response.status = 204
    await reconciler.reconcile([], db)
    assert NAME not in (gateway / "pjsip.conf").read_text()
