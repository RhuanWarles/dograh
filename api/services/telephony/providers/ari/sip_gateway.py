"""Provision SIP accounts on the bundled gateway from the ARI manager only.

The manager is the single writer. HTTP workers save tenant-scoped DB rows;
reconciliation picks up creates, edits, number assignments and deletions.
Gateway admin credentials are deployment secrets, never tenant credentials.
"""

import asyncio
import os
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import quote

import aiohttp
from loguru import logger

from api.services.telephony.providers.sip_trunk.sip_config import SIPTrunkConfiguration


def managed_connection(credentials: dict) -> dict:
    if not credentials.get("managed_sip"):
        return credentials

    name = credentials.get("stasis_app_name", "")

    return {
        **credentials,
        "ari_endpoint": os.environ.get("SIP_GATEWAY_ENDPOINT", ""),
        "app_name": "dograh",
        "app_password": os.environ.get("SIP_GATEWAY_PASSWORD", ""),
        "ws_client_name": "dograh",
        "dial_string_template": f"PJSIP/{{number}}@{name}",
    }


def gateway_enabled() -> bool:
    return all(
        os.environ.get(key)
        for key in (
            "SIP_GATEWAY_ENDPOINT",
            "SIP_GATEWAY_PASSWORD",
            "SIP_GATEWAY_CONFIG_DIR",
        )
    )


def _section(name: str, **values) -> str:
    # Asterisk's config parser treats an unescaped semicolon as a comment.
    def escape(value):
        return str(value).replace(";", r"\;")

    return f"\n[{name}]\n" + "".join(
        f"{key}={escape(value)}\n" for key, value in values.items()
    )


def render_account(
    credentials: dict, numbers: list[str], networks: list[str]
) -> tuple[str, str]:
    name = credentials["stasis_app_name"]
    if not re.fullmatch(r"dograh_[a-f0-9]{12}", name):
        raise ValueError("Invalid gateway account identifier")
    sip = SIPTrunkConfiguration.model_validate(credentials["sip"])
    if not networks:
        raise ValueError("No carrier addresses resolved; inbound access remains closed")
    domain = sip.from_domain or sip.host
    peer = f"sip:{sip.host}:{sip.port}"
    suffix = f";transport={sip.transport}"
    config = _section(
        f"{name}-auth",
        type="auth",
        auth_type="userpass",
        username=sip.auth_username or sip.username,
        password=sip.password,
    )
    config += _section(
        f"{name}-aor",
        type="aor",
        contact=peer + suffix,
        # qualify_frequency=30,
        # qualify_timeout=5,
    )
    config += _section(
        name,
        type="endpoint",
        transport=f"dograh-{sip.transport}",
        context=f"{name}-in",
        disallow="all",
        allow="ulaw,alaw",
        outbound_auth=f"{name}-auth",
        aors=f"{name}-aor",
        from_user=sip.username,
        from_domain=domain,
        direct_media="no",
        rtp_symmetric="yes",
        force_rport="yes",
        rewrite_contact="yes",
        dtmf_mode="rfc4733",
        send_pai="yes",
        trust_id_inbound="no",
        identify_by="ip,header,request_uri",
    )
    # ACL applies even when the registration's line parameter identifies the
    # endpoint. The carrier must originate from these IPs, never arbitrary peers.
    config += "deny=0.0.0.0/0\ndeny=::/0\n"
    config += "".join(f"permit={network}\n" for network in networks)
    # Request-URI contains our unguessable Contact user. Do NOT use a shared
    # carrier IP alone as the identifier: many tenants can use the same SBC.
    config += _section(
        f"{name}-identify",
        type="identify",
        endpoint=name,
        match_request_uri=f"/^sips?:{name}@/",
    )
    if sip.registration_enabled:
        config += _section(
            f"{name}-registration",
            type="registration",
            transport=f"dograh-{sip.transport}",
            outbound_auth=f"{name}-auth",
            server_uri=f"sip:{sip.registrar or sip.host}:{sip.port}" + suffix,
            client_uri=f"sip:{quote(sip.username, safe='+.-_')}@{domain}",
            contact_user=name,
            line="yes",
            endpoint=name,
            retry_interval=30,
            forbidden_retry_interval=300,
            expiration=300,
        )
    # Only registered phone numbers enter Stasis. Everything else terminates.
    destinations = sorted({n for n in numbers if re.fullmatch(r"\+?[0-9]{2,15}", n)})
    dialplan = f"\n[{name}-in]\n"
    for target in (name, "_+X.", "_X."):
        dialplan += f"exten => {target},1,Goto({name}-route,s,1)\n"
    dialplan += f"\n[{name}-route]\n"
    dialplan += "exten => s,1,Set(DOGRAH_TO=${PJSIP_HEADER(read,To)})\n"
    dialplan += " same => n,Set(DOGRAH_DID=${CUT(DOGRAH_TO,@,1)})\n"
    dialplan += " same => n,Set(DOGRAH_DID=${CUT(DOGRAH_DID,:,2)})\n"
    dialplan += f" same => n,Goto({name}-numbers,${{FILTER(0-9+,${{DOGRAH_DID}})}},1)\n"
    dialplan += f"\n[{name}-numbers]\n"
    for number in destinations:
        dialplan += f"exten => {number},1,Stasis({name})\n same => n,Hangup()\n"
        if number.startswith("+") and number[1:] not in destinations:
            dialplan += f"exten => {number[1:]},1,Goto({name}-numbers,{number},1)\n"
    # Registration-based carriers sometimes deliver the SIP account username
    # instead of the DID. There is a safe fallback only when one DID is assigned.
    if (
        len(destinations) == 1
        and sip.username not in destinations
        and re.fullmatch(r"[0-9]+", sip.username)
    ):
        if sip.username != destinations[0].lstrip("+"):
            dialplan += (
                f"exten => {sip.username},1,Goto({name}-numbers,{destinations[0]},1)\n"
            )
    dialplan += "exten => i,1,Hangup(1)\n"
    return config, dialplan


def _atomic_write(path: Path, text: str) -> None:
    # mkstemp creates the file with 0600, including while it contains secrets.
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SIPGatewayReconciler:
    def __init__(self):
        self._applied: tuple[str, str] | None = None

    async def reconcile(self, rows, db):
        if not gateway_enabled():
            return
        configs, dialplans = [], []
        for row in sorted(rows, key=lambda row: row.id):
            credentials = row.credentials or {}
            if row.provider != "sip_trunk":
                continue
            try:
                sip = SIPTrunkConfiguration.model_validate(credentials["sip"])
                if sip.inbound_networks:
                    networks = sip.inbound_networks.split(",")
                else:
                    addresses = await asyncio.wait_for(
                        asyncio.get_running_loop().getaddrinfo(
                            sip.host, sip.port, type=socket.SOCK_DGRAM
                        ),
                        timeout=5,
                    )
                    networks = sorted({f"{address[4][0]}/{sip.netmask}" for address in addresses})
                numbers = await db.list_phone_numbers_for_config(row.id)
                config, dialplan = render_account(
                    credentials,
                    [
                        number.address_normalized
                        for number in numbers
                        if number.is_active and number.inbound_workflow_id
                    ],
                    networks,
                )
                configs.append(config)
                dialplans.append(dialplan)
            except Exception:
                # Never log credential-bearing validation errors. Omitting a
                # broken account removes any previously provisioned routes.
                logger.error("SIP gateway could not provision configuration {}", row.id)
        desired = (
            "; Generated by Dograh\n" + "".join(configs),
            "; Generated by Dograh\n" + "".join(dialplans),
        )
        if desired == self._applied:
            return
        root = Path(os.environ["SIP_GATEWAY_CONFIG_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        for filename, text in zip(("pjsip.conf", "extensions.conf"), desired):
            _atomic_write(root / filename, text)
        auth = aiohttp.BasicAuth("dograh", os.environ["SIP_GATEWAY_PASSWORD"])
        endpoint = os.environ["SIP_GATEWAY_ENDPOINT"].rstrip("/")
        async with aiohttp.ClientSession(
            auth=auth, timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            for module in (
                "res_pjsip",
                "res_pjsip_outbound_registration",
                "pbx_config",
            ):
                async with session.put(
                    f"{endpoint}/ari/asterisk/modules/{module}.so"
                ) as response:
                    if response.status != 204:
                        raise RuntimeError(
                            f"SIP gateway reload failed (HTTP {response.status})"
                        )
        self._applied = desired
        logger.info("SIP gateway configuration synchronized")
