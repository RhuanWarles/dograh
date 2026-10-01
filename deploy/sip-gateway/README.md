# Managed SIP trunks

This optional Asterisk gateway lets each organization connect multiple carriers
using SIP username/password authentication. Each configuration represents one
carrier account and can own several phone numbers. The existing ARI integration
provides call control and bidirectional audio to Dograh.

## Local startup

Add a random `SIP_GATEWAY_PASSWORD` (32–128 letters, digits, `_` or `-`) to the
root `.env`, then build the application and gateway from this checkout:

```bash
docker compose -f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.sip.yaml up -d --build
```

Omit `docker-compose.override.yaml` on installations without that file. The
local override in this workspace supplies the locally built MinIO image.

The API, its ARI manager and gateway share the `sip_gateway_config` volume.
The gateway initializes generated files for the API's `dograh` user (UID 999).
Only the ARI manager writes SIP account configuration; changes are reconciled
within 60 seconds, including edits, inactive accounts and deletions. Gateway
admin credentials come from deployment environment variables and are never
stored in tenant configuration responses.

## Connect carriers and numbers

1. Open **Telephony → Add SIP trunk**.
2. Name the carrier account and enter its SIP server, port, transport, username
   and password. Use the separate authentication username, registrar and SIP
   domain fields only when required by the carrier.
3. Keep **Register with carrier** enabled for registration-based trunks.
4. Save, then add the carrier's phone numbers in international format, for
   example `+5511999990001`. Set a default caller ID for outgoing calls.
5. Choose an **Inbound workflow** for every number that should answer calls.
6. Check **SIP trunk connection** for the actual registration status. Repeat
   for additional carriers. Select the configuration when creating a campaign.

A successful REGISTER proves carrier authentication, not that the carrier has
allowed a particular caller ID, that its DID routing is correct, or that media
can traverse the network. Validate both directions with the real carrier.

## SIP routing and network requirements

- The gateway registers a unique Contact user and `line` parameter per account.
  Incoming calls must preserve the registered Contact/line, and carry the DID
  in the SIP `To` header. Only numbers assigned to that account and an inbound
  workflow are routed. This keeps multiple accounts on a shared carrier SBC
  separate. Carriers that replace the Contact/line need their inbound routing
  configured to use the contact user shown in Dograh.
- Inbound source ACLs default to the SIP server's resolved IP addresses. If the
  carrier sends calls from different SBCs, enter its supplied IPs/CIDRs in
  **Carrier inbound IPs / networks**. Never use an unrestricted network.
- UDP/TCP SIP uses port 5060; RTP uses UDP 10000–10100. The ARI admin port 8088
  is private to the Docker network. TLS is supported for outgoing connections
  to a carrier (normally remote port 5061); this setup does not publish a TLS
  SIP listener with a server certificate.
- For a host behind NAT, set `SIP_EXTERNAL_ADDRESS` in `.env` to the address
  reachable by the carrier, forward the SIP/RTP ports to this host, and recreate
  the gateway. Docker Desktop adds another network layer. A reachable server
  or a carrier VPN is usually needed when the host is behind CGNAT. An HTTP
  tunnel does not carry SIP/RTP.
- Codecs: G.711 µ-law and A-law; DTMF: RFC 4733. Use the carrier's required
  international number format; no carrier-specific prefix rewriting is applied.

## Verification

`api/tests/telephony/test_sip_gateway.py` covers validation, credential masking,
organization scoping, restricted origination and reconciliation. The UI tests
verify that SIP defaults are submitted and hidden ARI credentials are omitted.

`smoke_test.py` is an integration test for an isolated Docker network with a
`sip-gateway-test` container and a test-runner container named `sip-carrier-test`.
They share a disposable `/etc/asterisk/dograh` volume. The gateway password is
64 `a` characters **only in this isolated test**. Run the script from a Python
environment with the API dependencies and `api/.env.test` loaded. It implements
a fake Digest-authenticated registrar and tests two account registrations,
outbound origination, inbound number routing, cross-account rejection and a media
WebSocket connection, without real carrier calls. Give the test-runner container
the network alias `api` for the media test.

References: [Asterisk outbound registrations](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/Configuring-Outbound-Registrations/)
and [WebSocket media](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/).
