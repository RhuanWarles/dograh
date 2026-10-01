"""Start the bundled SIP/media gateway; ARI is only exposed on Docker's network."""

import os
import json
import signal
import subprocess
import threading
import time
import re
from pathlib import Path

root = Path("/etc/asterisk")
root.mkdir(parents=True, exist_ok=True)
generated = root / "dograh"
generated.mkdir(exist_ok=True)
os.chown(generated, 999, 999)
password = os.environ["SIP_GATEWAY_PASSWORD"]
if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", password):
    raise SystemExit("SIP_GATEWAY_PASSWORD must have 32–128 letters, digits, _ or -")
external = os.environ.get("SIP_EXTERNAL_ADDRESS", "")
if external and not re.fullmatch(r"[A-Za-z0-9.-]+", external):
    raise SystemExit("Invalid SIP_EXTERNAL_ADDRESS")


def write(name, text):
    path = root / name
    path.write_text(text.strip() + "\n")
    path.chmod(0o600)


write(
    "asterisk.conf",
    """
[directories]
astetcdir = /etc/asterisk
astmoddir = /usr/lib/asterisk/modules
astvarlibdir = /var/lib/asterisk
astdbdir = /var/lib/asterisk
astkeydir = /var/lib/asterisk
astdatadir = /var/lib/asterisk
astagidir = /var/lib/asterisk/agi-bin
astspooldir = /var/spool/asterisk
astrundir = /var/run/asterisk
astlogdir = /var/log/asterisk
[options]
verbose = 1
""",
)
write(
    "modules.conf",
    """
[modules]
autoload = yes
noload = chan_sip.so
noload = res_pjsip_endpoint_identifier_anonymous.so
noload = res_phoneprov.so
noload = res_hep.so
noload = res_hep_pjsip.so
noload = res_hep_rtcp.so
""",
)
write("http.conf", "[general]\nenabled=yes\nbindaddr=0.0.0.0\nbindport=8088")
write(
    "ari.conf",
    f"[general]\nenabled=yes\n[dograh]\ntype=user\nread_only=no\npassword={password}",
)
write("logger.conf", "[logfiles]\nconsole=notice,warning,error")
write("rtp.conf", "[general]\nrtpstart=10000\nrtpend=10100\nstrictrtp=yes")
write(
    "websocket_client.conf",
    """
[dograh]
type=websocket_client
uri=ws://api:8000/api/v1/telephony/ws/ari
protocols=media
connection_type=per_call_config
connection_timeout=10000
reconnect_attempts=0
""",
)
transports = (
    "[global]\ntype=global\nendpoint_identifier_order=request_uri,ip,header,username\n"
)
for transport in ("udp", "tcp", "tls"):
    port = 5061 if transport == "tls" else 5060
    transports += f"\n[dograh-{transport}]\ntype=transport\nprotocol={transport}\nbind=0.0.0.0:{port}\n"
    if external:
        transports += f"external_signaling_address={external}\nexternal_media_address={external}\n"
        transports += (
            "local_net=10.0.0.0/8\nlocal_net=172.16.0.0/12\nlocal_net=192.168.0.0/16\n"
        )
    if transport == "tls":
        transports += "method=tlsv1_2\nverify_server=yes\nca_list_file=/etc/ssl/certs/ca-certificates.crt\n"
write("pjsip.conf", transports + "\n#include dograh/pjsip.conf\n")
write(
    "extensions.conf",
    "[general]\nstatic=yes\nwriteprotect=yes\n#include dograh/extensions.conf\n",
)
for name in ("pjsip.conf", "extensions.conf"):
    path = generated / name
    if not path.exists():
        path.write_text("; Waiting for Dograh configuration\n")
        path.chmod(0o600)
        os.chown(path, 999, 999)
for directory in (
    "/var/run/asterisk",
    "/var/log/asterisk",
    "/var/spool/asterisk",
    "/var/lib/asterisk",
):
    Path(directory).mkdir(parents=True, exist_ok=True)


def monitor():
    while True:
        try:
            result = subprocess.run(
                ["asterisk", "-rx", "pjsip show registrations"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and (
                "Objects found:" in result.stdout
                or "No objects found." in result.stdout
            ):
                registrations = {}
                for line in result.stdout.splitlines():
                    match = re.search(
                        r"(dograh_[a-f0-9]{12})-registration/.*?\s(Registered|Unregistered|Rejected|Stopped)\b",
                        line,
                    )
                    if match:
                        registrations[match[1]] = match[2].lower()
                temporary = generated / "status.json.tmp"
                temporary.write_text(
                    json.dumps(
                        {"updated_at": time.time(), "registrations": registrations}
                    )
                )
                temporary.chmod(0o600)
                os.chown(temporary, 999, 999)
                temporary.replace(generated / "status.json")
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(5)


process = subprocess.Popen(["asterisk", "-f"])
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda signum, frame: process.send_signal(signum))
threading.Thread(target=monitor, daemon=True).start()
raise SystemExit(process.wait())
