"""Live tests against a real IOS device. Run manually: `pytest -m live -s`.

Configuration comes from environment variables, never from code:

    NETSDK_IOS_HOST         device to test (required)
    NETSDK_IOS_PORT         SSH port (default 22)
    NETSDK_USERNAME         read-only account (required)
    NETSDK_PASSWORD         password, for the password-login tests
    NETSDK_KEY_FILE         private key path, for the key-login test
    NETSDK_KEY_PASSPHRASE   key passphrase, if the key has one
    NETSDK_IOS_LONG_COMMAND command long enough to page (default "show interfaces")
    NETSDK_UNREACHABLE_HOST host that drops SSH (default 192.0.2.1, TEST-NET-1)
    NETSDK_SESSION_LOG_DIR  where session logs go (default "session_logs")
"""

import os
from pathlib import Path

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

needs_device = pytest.mark.skipif(
    not (HOST and USERNAME), reason="set NETSDK_IOS_HOST and NETSDK_USERNAME"
)
needs_password = pytest.mark.skipif(not PASSWORD, reason="set NETSDK_PASSWORD")
needs_key = pytest.mark.skipif(not KEY_FILE, reason="set NETSDK_KEY_FILE")


def password_creds() -> Credentials:
    assert USERNAME
    assert PASSWORD
    return Credentials(username=USERNAME, password=SecretStr(PASSWORD))


def device(creds: Credentials, host: str | None = None, timeout: float = 30.0) -> CiscoIOS:
    target = host or HOST
    assert target
    return CiscoIOS(target, creds, port=PORT, timeout=timeout, session_log_dir=LOG_DIR)


def assert_log_is_clean(log: Path | None) -> None:
    assert log is not None, "device has no session log"
    assert log.exists(), "session log was not written"
    text = log.read_bytes().decode("utf-8", "replace")
    if PASSWORD:
        assert PASSWORD not in text, "password leaked into the session log"
    print(f"\nsession log: {log}")


@needs_device
@needs_password
def test_password_login_show_version() -> None:
    with device(password_creds()) as dev:
        output = dev.send_command("show version")
        log = dev.session_log
    assert "Cisco IOS" in output
    assert "--More--" not in output
    assert_log_is_clean(log)


@needs_device
@needs_key
def test_key_login_show_version() -> None:
    assert USERNAME
    assert KEY_FILE
    creds = Credentials(
        username=USERNAME,
        key_file=Path(KEY_FILE),
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )
    with device(creds) as dev:
        output = dev.send_command("show version")
        log = dev.session_log
    assert "Cisco IOS" in output
    assert_log_is_clean(log)


@needs_device
@needs_password
def test_long_output_is_paged_cleanly() -> None:
    with device(password_creds(), timeout=120.0) as dev:
        output = dev.send_command(LONG_COMMAND)
        log = dev.session_log
    assert "--More--" not in output
    assert "\b" not in output
    assert "\r" not in output
    assert log is not None
    paged = "--More--" in log.read_bytes().decode("utf-8", "replace")
    print(f"\n{LONG_COMMAND!r}: {len(output.splitlines())} lines, pager hit: {paged}")
    print("last 5 lines:\n" + "\n".join(output.splitlines()[-5:]))


@needs_device
@needs_password
def test_show_terminal() -> None:
    # Tells us what width/length IOS gave the session (netmiko requests a
    # 511x1000 PTY); this decides how often the pager kicks in.
    with device(password_creds()) as dev:
        output = dev.send_command("show terminal")
    print(
        "\n"
        + "\n".join(line for line in output.splitlines() if "Length" in line or "Width" in line)
    )


@needs_device
@needs_password
def test_rejected_command_raises_and_session_survives() -> None:
    with device(password_creds()) as dev:
        with pytest.raises(CommandError) as excinfo:
            dev.send_command("show netsdk-no-such-command")
        print(f"\nrejected as: {excinfo.value}")
        assert dev.send_command("show clock")


@needs_device
def test_wrong_password_raises_authentication_error() -> None:
    # One deliberate failed login. Skip this if your AAA locks accounts quickly.
    assert USERNAME
    creds = Credentials(username=USERNAME, password=SecretStr("netsdk-wrong-password"))
    with pytest.raises(AuthenticationError):
        device(creds).open()


def test_unreachable_host_raises_connection_error() -> None:
    creds = Credentials(username=USERNAME or "nobody", password=SecretStr("x"))
    with pytest.raises(DeviceConnectionError) as excinfo:
        device(creds, host=UNREACHABLE_HOST, timeout=5.0).open()
    assert not isinstance(excinfo.value, AuthenticationError)
