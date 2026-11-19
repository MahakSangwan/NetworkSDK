import json
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import (
    CiscoNXOS,
    NXOSPortChannel,
    NXOSPortChannelMember,
    NXOSSSHTransport,
)
from netsdk.cisco.nxos.commands import SHOW_PORT_CHANNEL_SUMMARY
from netsdk.cisco.nxos.parsers import parse_show_port_channel_summary
from netsdk.core import LAG, Credentials, ParseError
from netsdk.core.testing import FakeCLI, FakeConnection

CREDS = Credentials(username="ro", password="pw")


def channel(
    name: str, group: int, status: str, members: list[tuple[str, str]], **extra: Any
) -> dict[str, Any]:
    """A ROW_channel with the fields seen live (2026-11-19); one member is an object."""
    rows: Any = [{"port": p, "port-status": s} for p, s in members]
    row: dict[str, Any] = {
        "group": str(group),
        "port-channel": name,
        "layer": "S",
        "status": status,
        "type": "Eth",
        "prtcl": "LACP",
    }
    if rows:
        row["TABLE_member"] = {"ROW_member": rows[0] if len(rows) == 1 else rows}
    return row | extra


def summary(*channels: dict[str, Any]) -> str:
    row: Any = channels[0] if len(channels) == 1 else list(channels)
    return json.dumps({"TABLE_channel": {"ROW_channel": row}})


def test_lacp_channel_with_members() -> None:
    (pc,) = parse_show_port_channel_summary(
        summary(channel("port-channel10", 10, "U", [("Ethernet1/1", "P"), ("Ethernet1/2", "s")]))
    )
    assert pc == NXOSPortChannel(
        name="port-channel10",
        oper_up=True,
        protocol="lacp",
        members=(
            NXOSPortChannelMember(interface="Ethernet1/1", bundled=True, status="P"),
            NXOSPortChannelMember(interface="Ethernet1/2", bundled=False, status="s"),
        ),
        group=10,
        layer="switched",
        status="U",
        protocol_raw="LACP",
    )
    assert isinstance(pc, LAG)


@pytest.mark.parametrize(
    ("flag", "bundled"),
    [
        ("P", True),
        ("p", True),
        ("D", False),
        ("s", False),
        ("r", False),
        ("I", False),
        ("H", False),
    ],
)
def test_member_flags(flag: str, bundled: bool) -> None:
    (pc,) = parse_show_port_channel_summary(
        summary(channel("port-channel1", 1, "U", [("Ethernet1/1", flag)]))
    )
    assert pc.members[0].bundled is bundled


def test_static_routed_down_channel_without_members() -> None:
    (pc,) = parse_show_port_channel_summary(
        summary(channel("port-channel920", 920, "D", [], layer="R", prtcl="NONE"))
    )
    assert (pc.oper_up, pc.protocol, pc.layer, pc.members) == (False, "static", "routed", ())


def test_short_names_are_expanded() -> None:
    (pc,) = parse_show_port_channel_summary(summary(channel("Po5", 5, "U", [("Eth1/5", "P")])))
    assert (pc.name, pc.members[0].interface) == ("port-channel5", "Ethernet1/5")


def test_unknown_protocol_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    (pc,) = parse_show_port_channel_summary(
        summary(channel("port-channel1", 1, "U", [], prtcl="PAgP"))
    )
    assert (pc.protocol, pc.protocol_raw) == (None, "PAgP")
    assert "unrecognised port-channel protocol 'PAgP'" in caplog.text


def test_no_port_channels() -> None:
    assert parse_show_port_channel_summary("") == []
    assert parse_show_port_channel_summary(json.dumps({})) == []


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(channel("", 1, "U", []), id="no-name"),
        pytest.param(channel("port-channel1", 1, "U", []) | {"group": "x"}, id="group"),
        pytest.param(
            {k: v for k, v in channel("port-channel1", 1, "U", []).items() if k != "status"},
            id="no-status",
        ),
        pytest.param(channel("port-channel1", 1, "U", [("", "P")]), id="member-without-port"),
    ],
)
def test_problems_are_parse_errors(row: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        parse_show_port_channel_summary(summary(row))


def test_duplicate_is_a_parse_error() -> None:
    row = channel("port-channel1", 1, "U", [])
    with pytest.raises(ParseError):
        parse_show_port_channel_summary(summary(row, row))


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_port_channels_sends_only_its_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = summary(channel("port-channel1", 1, "U", [("Ethernet1/1", "P")])) + "\r\n"
    conn = FakeCLI(
        {SHOW_PORT_CHANNEL_SUMMARY: [text]}, hostname="sw1", terminator="# "
    ).connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert [pc.name for pc in device.get_port_channels()] == ["port-channel1"]
    assert conn.writes == [f"{SHOW_PORT_CHANNEL_SUMMARY}\n", "exit\n"]
