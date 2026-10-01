"""Validated carrier credentials for the bundled Asterisk gateway."""

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class SIPTrunkConfiguration(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5060, ge=1, le=65535)
    transport: Literal["udp", "tcp", "tls"] = "udp"
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    netmask: int = 32
    auth_username: str = Field(default="", max_length=128)
    from_domain: str = Field(default="", max_length=253)
    registrar: str = Field(default="", max_length=253)
    registration_enabled: bool = True
    inbound_networks: str = Field(default="", max_length=2048)

    @field_validator("host", "from_domain", "registrar")
    @classmethod
    def hostname(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip().lower().rstrip(".")
        if info.field_name == "host" and not value:
            raise ValueError("SIP server is required")
        if value and not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
            raise ValueError(
                "Use a hostname or IPv4 address without sip:, port or path"
            )
        if value and any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in value.split(".")
        ):
            raise ValueError("Invalid SIP hostname")
        return value

    @field_validator("username", "auth_username")
    @classmethod
    def account(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if info.field_name == "username" and not value:
            raise ValueError("SIP username is required")
        if value and not re.fullmatch(r"[A-Za-z0-9_.+@-]+", value):
            raise ValueError(
                "SIP username may contain letters, numbers, _, ., +, @ and -"
            )
        return value

    @field_validator("password")
    @classmethod
    def single_line_secret(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("SIP password must not start or end with whitespace")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("SIP password must not contain control characters")
        return value

    @field_validator("inbound_networks")
    @classmethod
    def networks(cls, value: str) -> str:
        networks = []
        for item in value.split(","):
            if not item.strip():
                continue
            network = ipaddress.ip_network(item.strip(), strict=False)
            if network.prefixlen == 0:
                raise ValueError("Specify the carrier IPs, not an unrestricted network")
            networks.append(str(network))
        return ",".join(networks)
