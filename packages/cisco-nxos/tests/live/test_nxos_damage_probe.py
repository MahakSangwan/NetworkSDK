"""TEMPORARY diagnostic (2026-11-19): delete once the intermittent damage is fixed.

In the shared live session (24-line terminal), `show interface | json` on a
9.3/10.5 switch occasionally has a value broken across lines at a length
other than the 255-column terminal width, so parse_json marks it damaged.
This probe repeats the command in fresh sessions on a 24-line and a
1000-line terminal and, for every damaged break, prints the raw bytes
around it from the session log: letters masked as `a`, digits as `9`,
control characters shown.

    uv run pytest -m live -s -rs packages/cisco-nxos/tests/live -k damage_probe

Same environment variables as test_nxos_live.py.
"""

import os
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from netsdk.cisco.nxos import CiscoNXOS
from netsdk.cisco.nxos.commands import SHOW_INTERFACE
from netsdk.core import Credentials

pytestmark = pytest.mark.live

RUNS = 10


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_NXOS_HOST")
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[@-~]|[\x00-\x09\x0b-\x1f\x7f]")


def _mask(text: str) -> str:
    out, last = [], 0
    for match in _CONTROL.finditer(text):
        out.append(re.sub(r"[A-Za-z]", "a", re.sub(r"[0-9]", "9", text[last : match.start()])))
        out.append(match.group(0))
        last = match.end()
    out.append(re.sub(r"[A-Za-z]", "a", re.sub(r"[0-9]", "9", text[last:])))
    return repr("".join(out))


def _bad_breaks(output: str, width: int) -> list[tuple[int, str, str]]:
    """(line length, line tail, next line head) for breaks inside strings off the width."""
    found = []
    lines = output.split("\n")
    in_string = False
    for index, line in enumerate(lines):
        escaped = False
        for char in line:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = not in_string
        if in_string and index + 1 < len(lines) and len(line) != width:
            found.append((len(line), line[-30:], lines[index + 1][:30]))
    return found


@pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)
@pytest.mark.parametrize("height", [24, 1000])
def test_damage_probe(height: int) -> None:
    assert HOST
    assert USERNAME
    creds = Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        key_file=Path(KEY_FILE) if KEY_FILE else None,
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )
    damaged_runs = 0
    shown = 0
    for run in range(1, RUNS + 1):
        device = CiscoNXOS(
            HOST,
            creds,
            port=int(_env("NETSDK_NXOS_PORT") or 22),
            timeout=300.0,
            session_log_dir=Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs"),
            terminal_height=height,
        )
        with device:
            output = device.send_command(SHOW_INTERFACE)
        breaks = _bad_breaks(output, device.terminal_width)
        if not breaks:
            continue
        damaged_runs += 1
        assert device.session_log is not None
        raw = device.session_log.read_bytes().decode("utf-8", "replace")
        print(f"\nheight {height}, run {run}: {len(breaks)} damaged break(s)")
        for length, tail, head in breaks[:2]:
            print(f"  rendered line of {length} chars: {_mask(tail)} <break> {_mask(head)}")
            at = raw.find(head[:20]) if len(head) >= 20 else -1
            if at < 0:
                print("  (next line's start not found verbatim in the raw log)")
                continue
            print(f"  raw before: {_mask(raw[max(at - 120, 0) : at])}")
            print(f"  raw after:  {_mask(raw[at : at + 40])}")
            shown += 1
        if shown >= 4:
            break
    print(f"\nheight {height}: {damaged_runs} of {RUNS} runs damaged")
