"""TEMPORARY diagnostic (2026-11-18): delete once the 7.3 lost-character issue is fixed.

On 7.3(11)N1(1), long `show vlan brief | json` lines come out of our terminal
rendering cut at 255 characters with one character missing, yet SecureCRT
shows them whole. So the bytes are probably all there and our rendering
drops one. This probe reads the raw session log (exact bytes from the
switch, before any rendering) and prints:

- every control or escape sequence in the output, with counts;
- the raw bytes around the end of every raw line of 200+ characters.

Letters are masked as `a` and digits as `9`; punctuation (`/`, `-`, `,`)
and control characters are shown as they are.

    uv run pytest -m live -s -rs packages/cisco-nxos/tests/live -k raw_probe

Same environment variables as test_nxos_live.py.
"""

import os
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import SecretStr

from netsdk.cisco.nxos import CiscoNXOS
from netsdk.cisco.nxos.commands import SHOW_VLAN_BRIEF
from netsdk.core import Credentials

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_NXOS_HOST")
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")

# Escape sequences (CSI and two-byte ESC), and single control bytes but \r \n.
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[@-~]|\x1b[^\[]|[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")


def _mask(text: str) -> str:
    return re.sub(r"[A-Za-z]", "a", re.sub(r"[0-9]", "9", text))


def _show(text: str) -> str:
    """Masked, with escape sequences kept readable (their letters unmasked)."""
    out, last = [], 0
    for match in _CONTROL.finditer(text):
        out.append(_mask(text[last : match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_mask(text[last:]))
    return repr("".join(out))


@pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)
def test_raw_probe() -> None:
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
        device.send_command(SHOW_VLAN_BRIEF)
    log = device.session_log
    assert log is not None
    raw = log.read_bytes().decode("utf-8", "replace")
    start = raw.find(SHOW_VLAN_BRIEF)
    raw = raw[start:] if start >= 0 else raw

    print(f"\nsession log: {log} ({len(raw)} chars from the command on)")
    counts = Counter(m.group(0) for m in _CONTROL.finditer(raw))
    print("control/escape sequences (count):")
    for sequence, count in counts.most_common():
        print(f"  {sequence!r}: {count}")
    lone_cr = len(re.findall(r"\r(?!\n|\r)", raw))
    lone_lf = len(re.findall(r"(?<!\r)\n", raw))
    print(f"\\r not followed by \\n: {lone_cr}; \\n not preceded by \\r: {lone_lf}")

    lines = raw.split("\n")
    long_lines = [i for i, line in enumerate(lines) if len(line.rstrip("\r")) >= 200]
    lengths = Counter(len(lines[i].rstrip("\r")) for i in long_lines)
    print(f"raw lines of 200+ chars: {len(long_lines)}, by length: {dict(lengths.most_common(8))}")
    print("end of each long raw line | start of the next (first 8):")
    for i in long_lines[:8]:
        line = lines[i]
        following = lines[i + 1] if i + 1 < len(lines) else ""
        print(f"  {_show(line[-30:] + chr(10))} | {_show(following[:30])}")
    # Any control sequence inside a long line, with its surroundings.
    inside = [(i, m) for i in long_lines for m in _CONTROL.finditer(lines[i]) if m.start() > 0]
    print(f"control/escape sequences inside long raw lines: {len(inside)} (first 8):")
    for i, m in inside[:8]:
        line = lines[i]
        print(f"  at col {m.start()}: {_show(line[max(m.start() - 20, 0) : m.end() + 20])}")
