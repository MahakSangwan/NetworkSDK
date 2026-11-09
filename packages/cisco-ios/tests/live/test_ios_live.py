"""Live tests against a real IOS device. Run manually: `pytest -m live -s`.

Configuration comes from environment variables, never from code. Set a
password, a key, or both; tests use whatever is configured.

    NETSDK_IOS_HOST           device to test (required)
    NETSDK_IOS_PORT           SSH port (default 22)
    NETSDK_USERNAME           read-only account (required)
    NETSDK_PASSWORD           password
    NETSDK_KEY_FILE           private key path (OpenSSH format, not PuTTY .ppk)
    NETSDK_KEY_PASSPHRASE     key passphrase, if the key has one
    NETSDK_IOS_LONG_COMMAND   command long enough to page (default "show interfaces")
    NETSDK_UNREACHABLE_HOST   host that drops SSH (default 192.0.2.1, TEST-NET-1)
    NETSDK_SESSION_LOG_DIR    where session logs go (default "session_logs")
    NETSDK_LIVE_AUTH_FAILURE  set to 1 to run the deliberate failed-login test
                              (off by default: it can count towards AAA lockout)
"""

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from netsdk.cisco.ios import CiscoIOS
from netsdk.core import AuthenticationError, CommandError, Credentials, DeviceConnectionError

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_IOS_HOST")
PORT = int(_env("NETSDK_IOS_PORT") or 22)
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")
LONG_COMMAND = _env("NETSDK_IOS_LONG_COMMAND") or "show interfaces"
UNREACHABLE_HOST = _env("NETSDK_UNREACHABLE_HOST") or "192.0.2.1"
LOG_DIR = Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs")
AUTH_FAILURE_TEST = _env("NETSDK_LIVE_AUTH_FAILURE") == "1"

needs_device = pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_IOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)
needs_password = pytest.mark.skipif(not PASSWORD, reason="no NETSDK_PASSWORD set")
needs_key = pytest.mark.skipif(not KEY_FILE, reason="no NETSDK_KEY_FILE set")


def _key_kwargs() -> dict[str, object]:
    if not KEY_FILE:
        return {}
    return {
        "key_file": Path(KEY_FILE),
        "key_passphrase": SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    }


def configured_creds() -> Credentials:
    """Whatever is configured: password, key, or both."""
    assert USERNAME
    return Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        **_key_kwargs(),
    )


def device(
    creds: Credentials, host: str | None = None, timeout: float = 30.0, **kwargs: Any
) -> CiscoIOS:
    target = host or HOST
    assert target
    return CiscoIOS(target, creds, port=PORT, timeout=timeout, session_log_dir=LOG_DIR, **kwargs)


def assert_log_is_clean(log: Path | None) -> None:
    assert log is not None, "device has no session log"
    assert log.exists(), "session log was not written"
    text = log.read_bytes().decode("utf-8", "replace")
    for secret in (PASSWORD, KEY_PASSPHRASE):
        if secret:
            assert secret not in text, "a secret leaked into the session log"
    print(f"\nsession log: {log}")


# -- login, one test per auth method --------------------------------------------


@needs_device
@needs_password
def test_password_login_show_version() -> None:
    assert USERNAME
    assert PASSWORD
    creds = Credentials(username=USERNAME, password=SecretStr(PASSWORD))
    with device(creds) as dev:
        output = dev.send_command("show version")
        log = dev.session_log
    assert "Cisco IOS" in output
    assert_log_is_clean(log)


@needs_device
@needs_key
def test_key_login_show_version() -> None:
    assert USERNAME
    creds = Credentials(username=USERNAME, **_key_kwargs())
    with device(creds) as dev:
        output = dev.send_command("show version")
        log = dev.session_log
    assert "Cisco IOS" in output
    assert_log_is_clean(log)


# -- session behavior, with whatever credentials are configured ----------------


@needs_device
def test_long_output_is_paged_cleanly() -> None:
    # A 24-line terminal makes IOS page every ~23 lines, so the pager is
    # exercised on any device (the default 1000-line terminal rarely pages).
    with device(configured_creds(), timeout=120.0, terminal_height=24) as dev:
        output = dev.send_command(LONG_COMMAND)
        log = dev.session_log
    assert "--More--" not in output
    assert "\b" not in output
    assert "\r" not in output
    assert log is not None
    pages = log.read_bytes().decode("utf-8", "replace").count("--More--")
    print(f"\n{LONG_COMMAND!r}: {len(output.splitlines())} lines, pager prompts: {pages}")
    print("last 5 lines:\n" + "\n".join(output.splitlines()[-5:]))
    assert pages > 0, "the device never paged; is the command longer than 24 lines?"
    assert_log_is_clean(log)


# Tests below share one session: devices limit concurrent SSH sessions
# (often 5 vty lines) and some free a closed session's line only after a delay.


@pytest.fixture(scope="module")
def shared() -> Iterator[CiscoIOS]:
    with device(configured_creds(), timeout=120.0) as dev:
        yield dev
    assert_log_is_clean(dev.session_log)


@needs_device
def test_show_terminal(shared: CiscoIOS) -> None:
    # IOS takes width/length from the PTY we request (511x1000 by default).
    output = shared.send_command("show terminal")
    print(
        "\n"
        + "\n".join(line for line in output.splitlines() if "Length" in line or "Width" in line)
    )


@needs_device
def test_rejected_command_raises_and_session_survives(shared: CiscoIOS) -> None:
    with pytest.raises(CommandError) as excinfo:
        shared.send_command("show netsdk-no-such-command")
    print(f"\nrejected as: {excinfo.value}")
    assert shared.send_command("show clock")


@needs_device
def test_closed_session_frees_its_vty_line(shared: CiscoIOS) -> None:
    # Diagnostic: counts vty lines in use (no names or addresses printed).
    def vty_lines() -> int:
        try:
            output = shared.send_command("show users")
        except CommandError:
            pytest.skip("`show users` is not permitted for this account")
        return sum(" vty " in line for line in output.splitlines())

    before = vty_lines()
    with device(configured_creds()) as extra:
        during = vty_lines()
        extra.send_command("show clock")
    after = [vty_lines()]
    for _ in range(3):
        time.sleep(5)
        after.append(vty_lines())
    print(
        f"\nvty lines in use: before {before}, during {during}, after close (0/5/10/15 s) {after}"
    )
    assert during == before + 1, "the extra session didn't show up in `show users`"
    assert after[-1] == before, "the closed session still holds a vty line after 15 s"


# -- getters -------------------------------------------------------------------


@needs_device
def test_get_facts(shared: CiscoIOS) -> None:
    facts = shared.get_facts()
    print("\n" + "\n".join(f"{name}: {value}" for name, value in facts.model_dump().items()))
    assert facts.vendor == "Cisco"


@needs_device
def test_get_interfaces(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        interfaces = shared.get_interfaces()
    print(f"\n{len(interfaces)} interfaces")
    # No MACs or descriptions: this output gets pasted back for review.
    print(f"{'name':<28} {'en':<3} {'up':<3} {'speed':>10} {'duplex':<6} {'mtu':>5}")
    for i in interfaces:
        print(
            f"{i.name:<28} {i.enabled!s:<3.1} {i.oper_up!s:<3.1} {i.speed or '-':>10} "
            f"{i.duplex or '-':<6} {i.mtu or '-':>5}"
        )
    with_mac = sum(i.mac_address is not None for i in interfaces)
    print(f"{with_mac} of {len(interfaces)} have a MAC address")
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    assert interfaces


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
