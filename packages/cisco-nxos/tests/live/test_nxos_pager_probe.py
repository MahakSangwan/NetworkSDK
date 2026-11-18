"""TEMPORARY diagnostic (2026-11-18): delete once the 7.3 issue is fixed.

`get_vlans()` on 7.3(11)N1(1) read `Ethernet115-18` where `Ethernet1/15-18`
is expected. The first probe showed the pager is fine: the switch itself
breaks long JSON strings across lines, and a character seems to go missing
at the break. This probe prints every line break inside a JSON string of
`show vlan brief | json` (port lists) and `show interface switchport | json`
(VLAN lists), with 20 characters on each side. Only interface names and VLAN
numbers are in those strings; VLAN names and descriptions are masked.

    uv run pytest -m live -s -rs packages/cisco-nxos/tests/live -k pager_probe

Same environment variables as test_nxos_live.py.
"""

import os
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from netsdk.cisco.nxos import CiscoNXOS
from netsdk.cisco.nxos.commands import SHOW_INTERFACE_SWITCHPORT, SHOW_VLAN_BRIEF
from netsdk.core import Credentials

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_NXOS_HOST")
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")

#: Keys whose strings may hold names or descriptions: masked if they break.
_SENSITIVE_KEY = re.compile(r"name|desc", re.IGNORECASE)


def _mask(text: str) -> str:
    return re.sub(r"[A-Za-z]", "a", re.sub(r"[0-9]", "9", text))


def _breaks_inside_strings(output: str) -> list[tuple[str, int, str, str]]:
    """(key, length of the broken line, text before, text after) per break."""
    found = []
    lines = output.split("\n")
    in_string = False
    key = ""
    for index, line in enumerate(lines):
        escaped = False
        for char in line:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = not in_string
        if in_string and index + 1 < len(lines):
            keys = re.findall(r'"([^"]+)"\s*:', line) or [key]
            key = keys[-1]
            found.append((key, len(line), line[-20:], lines[index + 1][:20]))
    return found


@pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)
@pytest.mark.parametrize("command", [SHOW_VLAN_BRIEF, SHOW_INTERFACE_SWITCHPORT])
def test_pager_probe(command: str) -> None:
    assert HOST
    assert USERNAME
    creds = Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        key_file=Path(KEY_FILE) if KEY_FILE else None,
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )
    device = CiscoNXOS(
        HOST,
        creds,
        port=int(_env("NETSDK_NXOS_PORT") or 22),
        timeout=300.0,
        session_log_dir=Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs"),
    )
    with device:
        output = device.send_command(command)
    breaks = _breaks_inside_strings(output)
    print(f"\n{command!r}: {len(output.splitlines())} lines, {len(breaks)} breaks inside strings")
    print(f"lengths of the broken lines: {sorted({length for _, length, _, _ in breaks})}")
    print(f"keys with breaks: {sorted({key for key, _, _, _ in breaks})}")
    for key, _, before, after in breaks[:12]:
        if _SENSITIVE_KEY.search(key):
            before, after = _mask(before), _mask(after)
        print(f"  {key}: {before!r} <break> {after!r}")
