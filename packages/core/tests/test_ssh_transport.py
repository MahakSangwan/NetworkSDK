import socket
from pathlib import Path
from typing import Any, ClassVar

import paramiko
import pytest
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException

from netsdk.core import (
    AuthenticationError,
    CommandError,
    Credentials,
    DeviceConnectionError,
    DeviceTimeoutError,
    HostKeyError,
)
from netsdk.core.testing import FakeCLI, FakeConnection
from netsdk.core.transports import Transport
from netsdk.core.transports import ssh as ssh_module
from netsdk.core.transports.ssh import SSHTransport, _QuietConnection, render_terminal

CREDS = Credentials(username="ro", password="s3cret")
_KEY_A = paramiko.RSAKey.generate(1024)
_KEY_B = paramiko.RSAKey.generate(1024)


class PagedTransport(SSHTransport):
    pager_pattern = r" ?--More-- ?"
    error_patterns: ClassVar[tuple[str, ...]] = (r"^% Invalid input",)
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conn: FakeConnection,
    **kwargs: Any,
) -> PagedTransport:
    monkeypatch.setattr(PagedTransport, "_connect", lambda self, log_file: conn)
    return PagedTransport(
        "r1.example", CREDS, session_log=tmp_path / "logs" / "r1.log", timeout=1.0, **kwargs
    )


def test_satisfies_transport_protocol(tmp_path: Path) -> None:
    assert isinstance(SSHTransport("h", CREDS, session_log=tmp_path / "x.log"), Transport)


# -- render_terminal -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb\r\n", "a\nb\n"),
        ("a\r\r\nb", "a\nb"),
        (" --More-- " + "\b" * 10 + " " * 10 + "\b" * 10 + "next", "next"),
        (" --More-- \r          \rnext", "next"),
        ("abc\b\bX", "aXc"),
        ("plain line", "plain line"),
    ],
)
def test_render_terminal(raw: str, expected: str) -> None:
    assert render_terminal(raw) == expected


# -- open / prompt detection -------------------------------------------------


def test_open_reads_prompt_without_sending_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({})
    conn = cli.connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    assert transport.is_open
    assert transport.base_prompt == "R1"
    assert conn.writes == []


def test_open_sends_one_enter_when_device_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({}, terminator="#")
    conn = cli.connection(initial=[])
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    assert transport.base_prompt == "R1"
    assert conn.writes == ["\n"]


def test_open_fails_cleanly_when_no_prompt_ever_arrives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeConnection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.timeout = 0.1
    with pytest.raises(DeviceTimeoutError):
        transport.open()
    assert not transport.is_open
    assert conn.disconnected
    assert conn.writes == ["\n"]


def test_banner_lines_ending_in_hash_are_not_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({})
    conn = cli.connection(initial=["#####\r\n# Authorized use only #\r\n", "core-sw1>"])
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    assert transport.base_prompt == "core-sw1"


def test_session_log_file_is_created(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transport = make_transport(monkeypatch, tmp_path, FakeCLI({}).connection())
    transport.open()
    assert (tmp_path / "logs" / "r1.log").exists()
    transport.close()


# -- send_command ------------------------------------------------------------


def test_send_command_returns_output_without_echo_or_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({"show clock": ["*10:00:00.000 UTC Mon Nov 9 2026\r\n"]})
    conn = cli.connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    assert transport.send_command("show clock") == "*10:00:00.000 UTC Mon Nov 9 2026"
    assert conn.writes == ["show clock\n"]


@pytest.mark.parametrize(
    "erase",
    [
        "\b" * 10 + " " * 10 + "\b" * 10,
        "\r" + " " * 10 + "\r",
    ],
    ids=["backspace-erase", "carriage-return-erase"],
)
def test_pager_is_answered_and_erased(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, erase: str
) -> None:
    pages = ["line 1\r\nline 2\r\n", "line 3\r\nline 4\r\n", "line 5\r\n"]
    cli = FakeCLI({"show long": pages}, erase=erase)
    conn = cli.connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    output = transport.send_command("show long")
    assert output == "line 1\nline 2\nline 3\nline 4\nline 5"
    assert conn.writes == ["show long\n", " ", " "]


def test_pager_prompt_left_in_place_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({"show long": ["line 1\r\n", "line 2\r\n"]}, erase="\r\n")
    transport = make_transport(monkeypatch, tmp_path, cli.connection())
    transport.open()
    assert "--More--" not in transport.send_command("show long")


def test_rejected_command_raises_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = make_transport(monkeypatch, tmp_path, FakeCLI({}).connection())
    transport.open()
    with pytest.raises(CommandError) as excinfo:
        transport.send_command("show nonsense")
    assert excinfo.value.command == "show nonsense"
    assert excinfo.value.host == "r1.example"
    assert "% Invalid input" in (excinfo.value.output or "")


def test_error_text_deep_in_output_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "".join(f"log line {i}\r\n" for i in range(10)) + "% Invalid input in a log\r\n"
    transport = make_transport(
        monkeypatch, tmp_path, FakeCLI({"show logging": [body]}).connection()
    )
    transport.open()
    assert transport.send_command("show logging").endswith("% Invalid input in a log")


@pytest.mark.parametrize("command", ["", "   ", "show a\nshow b", "show a\r"])
def test_only_single_line_commands_are_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    conn = FakeCLI({}).connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    with pytest.raises(ValueError, match="single-line"):
        transport.send_command(command)
    assert conn.writes == []


def test_timeout_raises_and_blocks_further_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show slow": ["partial output\r\n"]}).connection()
    conn._responder = lambda data: [data.rstrip() + "\r\n", "partial output\r\n"]
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    with pytest.raises(DeviceTimeoutError):
        transport.send_command("show slow", timeout=0.05)
    with pytest.raises(DeviceConnectionError, match="out of sync"):
        transport.send_command("show clock")
    assert conn.writes == ["show slow\n"]


def test_device_closing_the_session_raises_connection_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({}).connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    conn.remote_conn.closed = True
    with pytest.raises(DeviceConnectionError, match="closed"):
        transport.send_command("show clock")


def test_send_command_requires_open(tmp_path: Path) -> None:
    transport = PagedTransport("r1", CREDS, session_log=tmp_path / "x.log")
    with pytest.raises(DeviceConnectionError, match="not open"):
        transport.send_command("show clock")


# -- close -------------------------------------------------------------------


def test_full_session_sends_only_our_commands_and_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = FakeCLI({"show version": ["Cisco IOS Software\r\n"]})
    conn = cli.connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.open()
    transport.send_command("show version")
    transport.close()
    assert conn.writes == ["show version\n", "exit\n"]
    assert conn.disconnected
    assert not transport.is_open


def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = FakeCLI({}).connection()
    transport = make_transport(monkeypatch, tmp_path, conn)
    transport.close()
    transport.open()
    transport.close()
    transport.close()
    assert conn.writes == ["exit\n"]


# -- netmiko integration -----------------------------------------------------


def test_quiet_connection_never_writes_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guards D16 against netmiko upgrades changing what the base class sends.
    conn = _QuietConnection(host="r1", username="ro", password="x", auto_connect=False)
    writes: list[str] = []
    monkeypatch.setattr(conn, "write_channel", writes.append)
    conn.remote_conn = None
    conn._try_session_preparation()
    conn.session_preparation()
    conn.disable_paging()
    conn.set_terminal_width()
    conn.cleanup()
    conn.is_alive()
    assert writes == []


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_unreachable_host_raises_connection_error(tmp_path: Path) -> None:
    transport = SSHTransport(
        "127.0.0.1",
        CREDS,
        session_log=tmp_path / "x.log",
        port=_closed_port(),
        timeout=2.0,
        known_hosts_file=tmp_path / "known_hosts",
    )
    with pytest.raises(DeviceConnectionError) as excinfo:
        transport.open()
    assert not isinstance(excinfo.value, AuthenticationError)
    assert not transport.is_open


def _raise_from(outer: Exception, inner: Exception) -> Any:
    def factory(**_kwargs: Any) -> Any:
        try:
            raise inner
        except Exception:
            raise outer  # noqa: B904 - mimics netmiko, which drops the cause

    return factory


@pytest.mark.parametrize(
    ("outer", "inner", "expected"),
    [
        (
            NetmikoAuthenticationException("Authentication to device failed."),
            paramiko.AuthenticationException("Authentication failed."),
            AuthenticationError,
        ),
        (
            NetmikoTimeoutException("A paramiko SSHException occurred"),
            paramiko.SSHException("Server '[r1]:22' not found in known_hosts"),
            HostKeyError,
        ),
        (
            NetmikoTimeoutException("A paramiko SSHException occurred"),
            paramiko.BadHostKeyException(
                "r1", paramiko.RSAKey.generate(1024), paramiko.RSAKey.generate(1024)
            ),
            HostKeyError,
        ),
        (
            NetmikoTimeoutException("TCP connection to device failed."),
            TimeoutError("timed out"),
            DeviceConnectionError,
        ),
    ],
    ids=["auth", "unknown-host-key-strict", "changed-host-key", "tcp-timeout"],
)
def test_netmiko_errors_are_translated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outer: Exception,
    inner: Exception,
    expected: type[Exception],
) -> None:
    monkeypatch.setattr(ssh_module, "_QuietConnection", _raise_from(outer, inner))
    transport = SSHTransport(
        "r1", CREDS, session_log=tmp_path / "x.log", known_hosts_file=tmp_path / "kh"
    )
    with pytest.raises(expected) as excinfo:
        transport.open()
    assert type(excinfo.value) is expected
    assert excinfo.value.__cause__ is not None


def test_missing_key_file_is_reported_before_connecting(tmp_path: Path) -> None:
    creds = Credentials(username="ro", key_file=tmp_path / "missing_key")
    transport = SSHTransport("r1", creds, session_log=tmp_path / "x.log")
    with pytest.raises(AuthenticationError, match="key file not found"):
        transport.open()


def test_new_host_key_is_saved_once(tmp_path: Path) -> None:
    known_hosts = tmp_path / "kh"
    known_hosts.touch()
    key = paramiko.RSAKey.generate(1024)
    ssh_transport = type("T", (), {"get_remote_server_key": lambda self: key})()
    client = type("C", (), {"get_transport": lambda self: ssh_transport})()
    conn = type("Conn", (), {"remote_conn_pre": client})()
    transport = SSHTransport(
        "r1", CREDS, session_log=tmp_path / "x.log", port=2222, known_hosts_file=known_hosts
    )
    transport._remember_host_key(conn, known_hosts)
    transport._remember_host_key(conn, known_hosts)
    saved = paramiko.HostKeys(str(known_hosts))
    assert saved.check("[r1]:2222", key)
    assert len(known_hosts.read_text().splitlines()) == 1
