from pathlib import Path

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport
from netsdk.core import CommandError, Credentials, DeviceConnectionError
from netsdk.core.testing import FakeCLI, FakeConnection

CREDS = Credentials(username="ro", password="pw")

SHOW_VERSION = (
    "Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E4\r\n"
    "Technical Support: http://www.cisco.com/techsupport\r\n"
)


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS(
        "sw1",
        CREDS,
        timeout=1.0,
        session_log_dir=tmp_path / "logs",
        known_hosts_file=tmp_path / "known_hosts",
    )


def test_session_sends_only_requested_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({"show version": [SHOW_VERSION]}, hostname="sw1", terminator="#")
    conn = cli.connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        output = device.send_command("show version")
    assert output.startswith("Cisco IOS Software, C2960X")
    assert conn.writes == ["show version\n", "exit\n"]
    assert not device.is_open


def test_ios_pager_is_handled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pages = [
        "".join(f"GigabitEthernet1/0/{i} is up\r\n" for i in range(1, 23)),
        "".join(f"GigabitEthernet1/0/{i} is up\r\n" for i in range(23, 49)),
    ]
    # IOS erases " --More-- " with backspaces, spaces, backspaces.
    cli = FakeCLI({"show interfaces": pages}, hostname="sw1")
    conn = cli.connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        output = device.send_command("show interfaces")
    assert output.splitlines() == [f"GigabitEthernet1/0/{i} is up" for i in range(1, 49)]
    assert conn.writes == ["show interfaces\n", " ", "exit\n"]


@pytest.mark.parametrize(
    "error",
    [
        "                ^\r\n% Invalid input detected at '^' marker.\r\n",
        "% Incomplete command.\r\n",
        '% Ambiguous command:  "show i"\r\n',
        "Command authorization failed.\r\n",
        "% Authorization failed.\r\n",
    ],
)
def test_ios_errors_raise_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: str
) -> None:
    conn = FakeCLI({"show x": [error]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError):
        device.send_command("show x")


def test_session_log_is_per_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    device = make_device(monkeypatch, tmp_path, FakeCLI({}, hostname="sw1").connection())
    with device:
        log = device.session_log
    assert log is not None
    assert log.parent == tmp_path / "logs"
    assert log.name.startswith("sw1_")


def test_send_command_on_closed_device_raises(tmp_path: Path) -> None:
    device = CiscoIOS("sw1", CREDS, session_log_dir=tmp_path)
    with pytest.raises(DeviceConnectionError, match="not open"):
        device.send_command("show version")
