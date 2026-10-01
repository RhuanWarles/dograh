"""Isolated SIP carrier: validates Digest REGISTER and both call directions."""

import asyncio
import hashlib
import re
import socket
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from api.services.telephony.providers.ari.sip_gateway import render_account

NAME = "dograh_" + secrets.token_hex(6)
OTHER = "dograh_" + secrets.token_hex(6)
PASSWORD = "carrier-secret;with-comment"
REALM = "dograh-smoke"
NONCE = "fixed-test-nonce"


class Carrier(asyncio.DatagramProtocol):
    def __init__(self):
        self.registered = asyncio.Event()
        self.dialed = asyncio.Event()
        self.contact = None
        self.error = None
        self.registrations = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        text = data.decode()
        first, *lines = text.split("\r\n")
        headers = dict(
            (k.lower(), v.strip())
            for line in lines
            if ": " in line
            for k, v in [line.split(": ", 1)]
        )
        if first.startswith("SIP/2.0"):
            return
        method = first.split()[0]
        status, extra = "200 OK", ""
        if method == "REGISTER":
            auth = headers.get("authorization")
            if not auth:
                status = "401 Unauthorized"
                extra = f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}", algorithm=MD5, qop="auth"\r\n'
            else:
                fields = {}
                for match in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', auth):
                    fields[match[1]] = match[2] if match[2] is not None else match[3]
                md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
                a1 = md5(f"{fields['username']}:{REALM}:{PASSWORD}")
                a2 = md5(f"REGISTER:{fields['uri']}")
                expected = md5(
                    f"{a1}:{NONCE}:{fields['nc']}:{fields['cnonce']}:auth:{a2}"
                )
                if fields.get("response") != expected:
                    self.error = "Digest authentication mismatch"
                    status = "403 Forbidden"
                else:
                    if headers.get("expires") != "0":
                        self.registrations.add(fields["username"])
                    if fields["username"] == "carrier-a":
                        self.contact = re.search(r"<([^>]+)>", headers["contact"])[1]
                    if len(self.registrations) == 2:
                        self.registered.set()
                extra = f"Contact: {headers['contact']}\r\nExpires: 300\r\n"
        if method == "INVITE":
            if "+5511888880000@" not in first or "+5511999990001" not in headers.get(
                "p-asserted-identity", ""
            ):
                self.error = "Incorrect outbound destination or caller ID"
            self.dialed.set()
            status = "486 Busy Here"
        if method == "ACK":
            return
        response = f"SIP/2.0 {status}\r\n"
        for key in ("via", "from", "to", "call-id", "cseq"):
            response += (
                f"{key}: {headers[key]}"
                + (
                    ";tag=carrier"
                    if key == "to" and ";tag=" not in headers[key]
                    else ""
                )
                + "\r\n"
            )
        response += extra + "Content-Length: 0\r\n\r\n"
        self.transport.sendto(response.encode(), addr)


async def main():
    loop = asyncio.get_running_loop()
    transport, carrier = await loop.create_datagram_endpoint(
        Carrier, local_addr=("0.0.0.0", 5060)
    )
    media_connected = asyncio.Event()

    async def media_handler(request):
        ws = web.WebSocketResponse(protocols=["media"])
        await ws.prepare(request)
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT and "MEDIA_START" in message.data:
                assert request.query.get("smoke") == "yes"
                media_connected.set()
                await ws.send_bytes(bytes([255]) * 160)
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/telephony/ws/ari", media_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    my_ip = socket.gethostbyname(socket.gethostname())
    credentials = lambda name, user: {
        "stasis_app_name": name,
        "connection_mode": "sip_trunk",
        "sip": {"host": "sip-carrier-test", "username": user, "password": PASSWORD},
    }
    first, first_dialplan = render_account(
        credentials(NAME, "carrier-a"), ["+5511999990001"], [my_ip]
    )
    second, second_dialplan = render_account(
        credentials(OTHER, "carrier-b"), ["+5511999990002"], [my_ip]
    )
    Path("/etc/asterisk/dograh/pjsip.conf").write_text(first + second)
    Path("/etc/asterisk/dograh/extensions.conf").write_text(
        first_dialplan + second_dialplan
    )
    auth = aiohttp.BasicAuth("dograh", "a" * 64)
    async with aiohttp.ClientSession(auth=auth) as session:
        base = "http://sip-gateway-test:8088/ari"
        for _ in range(30):
            try:
                async with session.get(base + "/asterisk/info") as response:
                    if response.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
        for module in ("res_pjsip", "res_pjsip_outbound_registration", "pbx_config"):
            async with session.put(
                base + "/asterisk/modules/" + module + ".so"
            ) as response:
                assert response.status == 204, (
                    module,
                    response.status,
                    await response.text(),
                )
        await asyncio.wait_for(carrier.registered.wait(), 40)
        assert carrier.error is None, carrier.error
        print(
            "PASS: two SIP accounts registered with Digest authentication, including a semicolon in the password",
            flush=True,
        )
        async with session.ws_connect(
            base + "/events?app=" + NAME + "," + OTHER
        ) as events:
            async with session.post(
                base + "/channels",
                params={
                    "endpoint": f"PJSIP/+5511888880000@{NAME}",
                    "app": NAME,
                    "callerId": "+5511999990001",
                },
            ) as response:
                assert response.status == 200, await response.text()
            await asyncio.wait_for(carrier.dialed.wait(), 10)
            assert carrier.error is None, carrier.error
            print(
                "PASS: outbound INVITE uses the selected trunk, destination and caller ID",
                flush=True,
            )
            gateway_ip = socket.gethostbyname("sip-gateway-test")
            sdp = f"v=0\r\no=test 1 1 IN IP4 {my_ip}\r\ns=test\r\nc=IN IP4 {my_ip}\r\nt=0 0\r\nm=audio 12000 RTP/AVP 0 8\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n"
            invite = f"INVITE {carrier.contact} SIP/2.0\r\nVia: SIP/2.0/UDP {my_ip}:5060;branch=z9hG4bK-smoke1;rport\r\nMax-Forwards: 70\r\nFrom: <sip:+5511777770000@sip-carrier-test>;tag=caller\r\nTo: <sip:+5511999990001@sip-carrier-test>\r\nCall-ID: smoke-inbound-1\r\nCSeq: 1 INVITE\r\nContact: <sip:caller@{my_ip}:5060>\r\nContent-Type: application/sdp\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}"
            transport.sendto(invite.encode(), (gateway_ip, 5060))
            for _ in range(25):
                event = await asyncio.wait_for(events.receive_json(), 10)
                if (
                    event["type"] == "StasisStart"
                    and event["channel"]["dialplan"]["exten"] == "+5511999990001"
                ):
                    assert event["application"] == NAME
                    async with session.post(
                        base + "/channels/externalMedia",
                        params={
                            "app": NAME,
                            "external_host": "dograh",
                            "format": "ulaw",
                            "transport": "websocket",
                            "encapsulation": "none",
                            "connection_type": "client",
                            "direction": "both",
                            "transport_data": "v(smoke=yes)",
                        },
                    ) as response:
                        assert response.status == 200, await response.text()
                        media_channel = await response.json()
                    await asyncio.wait_for(media_connected.wait(), 10)
                    print(
                        "PASS: media WebSocket opens with per-call variables and accepts audio frames",
                        flush=True,
                    )
                    await session.delete(base + "/channels/" + media_channel["id"])
                    await session.delete(base + "/channels/" + event["channel"]["id"])
                    print(
                        "PASS: inbound INVITE reaches the assigned number and only its Stasis application",
                        flush=True,
                    )
                    wrong = (
                        invite.replace("+5511999990001", "+5511999990002")
                        .replace("smoke-inbound-1", "smoke-inbound-2")
                        .replace("z9hG4bK-smoke1", "z9hG4bK-smoke2")
                    )
                    transport.sendto(wrong.encode(), (gateway_ip, 5060))
                    try:
                        async with asyncio.timeout(2):
                            while True:
                                unexpected = await events.receive_json()
                                assert not (
                                    unexpected["type"] == "StasisStart"
                                    and unexpected["channel"]["dialplan"]["exten"]
                                    == "+5511999990002"
                                ), "Cross-account routing"
                    except TimeoutError:
                        pass
                    print(
                        "PASS: the first account cannot route an inbound call to the second account number",
                        flush=True,
                    )
                    break
            else:
                raise AssertionError("Inbound call not routed")
    await runner.cleanup()
    transport.close()


if __name__ == "__main__":
    asyncio.run(main())
