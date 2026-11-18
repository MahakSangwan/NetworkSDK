"""TEMPORARY diagnostic (2026-11-18): delete once the 7.3 pager issue is fixed.

`get_vlans()` on 7.3(11)N1(1) read `Ethernet115-18` where `Ethernet1/15-18`
is expected. Suspected cause: a `--More--` prompt in the middle of a line
longer than the terminal, whose erase backs up one column too far. This
test runs `show vlan brief | json` in its own session on a 24-line terminal,
then prints the bytes around every pager prompt that isn't at a line start,
with letters masked as `a` and digits as `9` (no names or numbers).

    uv run pytest -m live -s -rs packages/cisco-nxos/tests/live -k pager_probe

Same environment variables as test_nxos_live.py.
"""

import os
import re
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


def _mask(text: str) -> str:
    return re.sub(r"[A-Za-z]", "a", re.sub(r"[0-9]", "9", text))


@pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)
def test_pager_probe() -> None:
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
        terminal_height=24,
    )
    with device:
        output = device.send_command(SHOW_VLAN_BRIEF)
    log = device.session_log
    assert log is not None
    # The session log is flushed on close, so it is complete here.
    data = log.read_bytes().decode("utf-8", "replace")
    prompts = list(re.finditer("--More--", data))
    mid_line = [m for m in prompts if not data[: m.start()].rstrip(" ").endswith("\n")]
    longest = max((len(line) for line in data.split("\n")), default=0)
    print(f"\nsession log: {log}")
    print(f"pager prompts: {len(prompts)}, not at a line start: {len(mid_line)}")
    print(f"longest raw line: {longest} chars")
    print("15 chars before | the prompt | 35 chars after (control characters shown):")
    for m in mid_line[:10]:
        before = _mask(data[max(m.start() - 15, 0) : m.start()])
        after = _mask(data[m.end() : m.end() + 35])
        print(f"  {before!r} | {data[m.start() : m.end()]!r} | {after!r}")
    # Same for the first few prompts at a line start, for comparison.
    for m in [p for p in prompts if p not in mid_line][:3]:
        print(f"  line start: {_mask(data[m.end() : m.end() + 35])!r}")
    # And the bad port names as they came out (masked), if any.
    bad = sorted({_mask(t) for t in re.findall(r"Ethernet\d+-\d+", output)})
    print(f"port names with a range but no '/' (masked): {bad or 'none'}")
