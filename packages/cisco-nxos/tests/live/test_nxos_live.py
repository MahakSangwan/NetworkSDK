"""Live tests against a real NX-OS switch. Run manually: `pytest -m live -s`.

Configuration comes from environment variables, never from code. Set a
password, a key, or both; tests log in with whatever is configured.

    NETSDK_NXOS_HOST          switch to test (required; standalone NX-OS, not ACI)
    NETSDK_NXOS_PORT          SSH port (default 22)
    NETSDK_USERNAME           read-only account (required)
    NETSDK_PASSWORD           password
    NETSDK_KEY_FILE           private key path (OpenSSH format, not PuTTY .ppk)
    NETSDK_KEY_PASSPHRASE     key passphrase, if the key has one
    NETSDK_NXOS_LONG_COMMAND  command long enough to page (default "show interface")
    NETSDK_UNREACHABLE_HOST   host that drops SSH (default 192.0.2.1, TEST-NET-1)
    NETSDK_SESSION_LOG_DIR    where session logs go (default "session_logs")
    NETSDK_LIVE_AUTH_FAILURE  set to 1 to run the deliberate failed-login test
                              (off by default: it can count towards AAA lockout)

Everything but the failure tests runs in one shared session. Printed output
has no hostnames, serials or addresses: it gets pasted back for review.
"""

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from netsdk.cisco.nxos import CiscoNXOS
from netsdk.cisco.nxos.commands import SHOW_INVENTORY, SHOW_VERSION
from netsdk.cisco.nxos.parsers import rows, text
from netsdk.core import AuthenticationError, CommandError, Credentials, DeviceConnectionError
from netsdk.core.parsing import parse_json

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_NXOS_HOST")
PORT = int(_env("NETSDK_NXOS_PORT") or 22)
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")
LONG_COMMAND = _env("NETSDK_NXOS_LONG_COMMAND") or "show interface"
UNREACHABLE_HOST = _env("NETSDK_UNREACHABLE_HOST") or "192.0.2.1"
LOG_DIR = Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs")
AUTH_FAILURE_TEST = _env("NETSDK_LIVE_AUTH_FAILURE") == "1"

#: A 24-line terminal so the switch pages often and every test meets the pager.
PAGED_HEIGHT = 24

needs_device = pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)


def configured_creds() -> Credentials:
    assert USERNAME
    return Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        key_file=Path(KEY_FILE) if KEY_FILE else None,
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )


def device(
    creds: Credentials, host: str | None = None, timeout: float = 30.0, **kwargs: Any
) -> CiscoNXOS:
    target = host or HOST
    assert target
    return CiscoNXOS(target, creds, port=PORT, timeout=timeout, session_log_dir=LOG_DIR, **kwargs)


@pytest.fixture(scope="module")
def shared() -> Iterator[CiscoNXOS]:
    with device(configured_creds(), timeout=120.0, terminal_height=PAGED_HEIGHT) as dev:
        yield dev


# -- session behaviour -----------------------------------------------------------


@needs_device
def test_login(shared: CiscoNXOS) -> None:
    methods = [name for name, value in (("password", PASSWORD), ("key", KEY_FILE)) if value]
    print(f"\nlogged in with: {' or '.join(methods)}")
    assert shared.send_command("show clock")


@needs_device
def test_show_terminal(shared: CiscoNXOS) -> None:
    # Reported, not asserted: whether NX-OS takes its length from our PTY.
    output = shared.send_command("show terminal")
    sizes = [line for line in output.splitlines() if "Length" in line or "Width" in line]
    print("\n" + ("\n".join(sizes) or "no Length/Width lines; full output:\n" + output))


@needs_device
def test_long_output_is_paged_cleanly(shared: CiscoNXOS) -> None:
    output = shared.send_command(LONG_COMMAND)
    lines = output.splitlines()
    print(f"\n{LONG_COMMAND!r}: {len(lines)} lines on a {PAGED_HEIGHT}-line terminal")
    assert len(lines) > PAGED_HEIGHT, "output fits one page; set NETSDK_NXOS_LONG_COMMAND"
    for bad in ("--More--", "\b", "\r", "\x1b"):
        assert bad not in output, f"{bad!r} left in the output"


@needs_device
def test_rejected_command_raises_and_session_survives(shared: CiscoNXOS) -> None:
    with pytest.raises(CommandError) as excinfo:
        shared.send_command("show netsdk-no-such-command")
    print(f"\nrejected as: {excinfo.value}")
    assert shared.send_command("show clock")


# -- getters -------------------------------------------------------------------


@needs_device
def test_json_layout(shared: CiscoNXOS, caplog: pytest.LogCaptureFixture) -> None:
    # Key names only (no values): confirms the fields get_facts() reads.
    with caplog.at_level(logging.INFO, logger="netsdk"):
        raw = shared.get_facts_raw()
        version = parse_json(SHOW_VERSION, raw[SHOW_VERSION])
        inventory = parse_json(SHOW_INVENTORY, raw[SHOW_INVENTORY])
    for command, output in raw.items():
        print(f"\n{command!r}: {len(output.splitlines())} line(s)")
    print(f"show version keys: {', '.join(sorted(version))}")
    print(f"show inventory keys: {', '.join(sorted(inventory))}")
    records = rows(inventory, "TABLE_inv", "ROW_inv", SHOW_INVENTORY)
    print(f"{len(records)} inventory entries")
    print(f"{'name':<34} {'part id':<24} {'sn':<3} description")
    for r in records:
        print(
            f"{json.dumps(r.get('name')):<34} {text(r, 'productid') or '-':<24} "
            f"{'set' if text(r, 'serialnum') else '-':<3} {text(r, 'desc') or '-'}"
        )
    for record in caplog.records:
        print(f"{record.levelname}: {record.getMessage()}")
    assert records


@needs_device
def test_get_facts(shared: CiscoNXOS) -> None:
    facts = shared.get_facts()
    shown = facts.model_dump(exclude={"hostname", "serial_number"})
    print("\nhostname: set\nserial_number: set")
    print("\n".join(f"{name}: {value}" for name, value in shown.items()))
    # Reported, not asserted: whether the board ID in `show version` is the
    # chassis serial (expected on fixed switches, not on modular ones).
    board = text(parse_json(SHOW_VERSION, shared.get_facts_raw()[SHOW_VERSION]), "proc_board_id")
    print(f"show version board ID is the chassis serial: {board == facts.serial_number}")
    assert facts.vendor == "Cisco"


@needs_device
def test_shared_session_log_is_complete_and_clean(shared: CiscoNXOS) -> None:
    # Last shared test: logs out, then checks the whole session's log.
    shared.close()
    log = shared.session_log
    assert log is not None, "device has no session log"
    assert log.exists(), "session log was not written"
    data = log.read_bytes().decode("utf-8", "replace")
    for secret in (PASSWORD, KEY_PASSPHRASE):
        if secret:
            assert secret not in data, "a secret leaked into the session log"
    print(f"\nsession log: {log}")
    print(f"pager prompts answered: {data.count('--More--')}")
    assert "--More--" in data
    assert "exit" in data[-200:], "the log doesn't end with our logout"


# -- failures ------------------------------------------------------------------


@needs_device
@pytest.mark.skipif(not AUTH_FAILURE_TEST, reason="set NETSDK_LIVE_AUTH_FAILURE=1 to run")
def test_wrong_password_raises_authentication_error() -> None:
    assert USERNAME
    creds = Credentials(username=USERNAME, password=SecretStr("netsdk-wrong-password"))
    with pytest.raises(AuthenticationError):
        device(creds).open()


def test_unreachable_host_raises_connection_error() -> None:
    # Never reaches a device, so it can't affect AAA.
    creds = Credentials(username=USERNAME or "nobody", password=SecretStr("x"))
    with pytest.raises(DeviceConnectionError) as excinfo:
        device(creds, host=UNREACHABLE_HOST, timeout=5.0).open()
    assert not isinstance(excinfo.value, AuthenticationError)
