import json
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSInterface, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_INTERFACE
from netsdk.cisco.nxos.parsers import parse_show_interface
from netsdk.core import Credentials, ParseError, SupportsInterfaces
from netsdk.core.testing import FakeCLI, FakeConnection

NAPALM = Path(__file__).parent / "fixtures" / "napalm"
CREDS = Credentials(username="ro", password="pw")


def by_name(name: str) -> dict[str, NXOSInterface]:
    return {i.name: i for i in parse_show_interface((NAPALM / name).read_text())}


def one(record: dict[str, Any]) -> NXOSInterface:
    (interface,) = parse_show_interface(json.dumps({"TABLE_interface": {"ROW_interface": record}}))
    return interface


ETH = {
    "interface": "Ethernet1/1",
    "state": "up",
    "admin_state": "up",
    "eth_hw_desc": "100/1000/10000 Ethernet",
    "eth_hw_addr": "18e7.2813.5029",
    "eth_mtu": "1500",
    "eth_bw": 10000000,
    "eth_duplex": "full",
    "eth_speed": "10 Gb/s",
}


def test_nexus3548_sample() -> None:
    interfaces = by_name("ssh_Nexus3548_show_interface_json.txt")
    assert len(interfaces) == 62
    assert interfaces["Ethernet1/2"] == NXOSInterface(
        name="Ethernet1/2",
        description="SIL-VOLT-202_SF_P2",
        enabled=True,
        oper_up=True,
        mac_address="18:e7:28:13:50:29",
        mtu=9216,
        speed=10_000_000,
        duplex="full",
        hardware_type="100/1000/10000 Ethernet",
        burned_in_address="18:e7:28:13:50:29",
        bandwidth=10_000_000,
        port_mode="trunk",
    )
    # Admin up, no transceiver: down, with the reason.
    empty_slot = interfaces["Ethernet1/1"]
    assert (empty_slot.enabled, empty_slot.oper_up) == (True, False)
    assert empty_slot.state_reason == "XCVR not inserted"
    assert (empty_slot.speed, empty_slot.duplex) == (None, "auto")  # "auto-speed"
    channel = interfaces["port-channel69"]
    assert (channel.hardware_type, channel.speed, channel.mtu) == ("Port-Channel", 10_000_000, 9216)
    assert interfaces["mgmt0"].speed == 1_000_000
    svi = interfaces["Vlan1"]
    assert (svi.enabled, svi.oper_up, svi.state_reason) == (False, False, "Administratively down")
    assert (svi.speed, svi.duplex, svi.hardware_type, svi.port_mode) == (None, None, None, None)


def test_normal_sample() -> None:
    interfaces = by_name("nxapi_normal_show_interface.json")
    assert len(interfaces) == 146
    shut = interfaces["Ethernet2/7"]
    assert (shut.enabled, shut.oper_up, shut.state_reason) == (
        False,
        False,
        "Administratively down",
    )
    assert interfaces["Vlan1"].mac_address == "2c:c2:60:1c:7b:94"


def test_svi_sample() -> None:
    interfaces = by_name("nxapi_vlan_interface_show_interface.json")
    assert interfaces["Vlan120"].model_dump(include={"enabled", "oper_up", "mtu"}) == {
        "enabled": True,
        "oper_up": True,
        "mtu": 1500,
    }
    assert interfaces["Vlan120"].description is None  # "svi_desc": ""
    assert interfaces["Vlan199"].description == "This is a description"


def test_admin_up_oper_down_sample() -> None:
    interfaces = by_name("nxapi_admin_up_oper_down_show_interface.json")
    assert [(i.name, i.enabled, i.oper_up) for i in interfaces.values()] == [
        ("Ethernet1/1", True, False),
        ("Ethernet1/2", False, False),
    ]


@pytest.mark.parametrize(
    ("speed", "kbps"),
    [("1000 Mb/s", 1_000_000), ("25 Gb/s", 25_000_000), ("100 Gb/s", 100_000_000)],
)
def test_speeds(speed: str, kbps: int) -> None:
    assert one(ETH | {"eth_speed": speed}).speed == kbps


def test_unknown_speed_and_duplex_are_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    interface = one(ETH | {"eth_speed": "fast", "eth_duplex": "sideways"})
    assert (interface.speed, interface.duplex) == (None, None)
    assert "unrecognised speed 'fast'" in caplog.text
    assert "unrecognised duplex 'sideways'" in caplog.text


# 7.3(11)N1(1) has no admin_state (field names from the live run, 2026-11-13).
ETH_73 = {k: v for k, v in ETH.items() if k != "admin_state"} | {"share_state": "Dedicated"}


@pytest.mark.parametrize(
    ("state", "reason", "enabled", "oper_up"),
    [
        ("up", None, True, True),
        ("down", "SFP not inserted", True, False),
        ("down", "Link not connected", True, False),
        ("down", "Administratively down", False, False),
        ("down", None, True, False),
    ],
)
def test_73_without_admin_state(
    state: str, reason: str | None, enabled: bool, oper_up: bool
) -> None:
    record = ETH_73 | {"state": state} | ({"state_rsn_desc": reason} if reason else {})
    interface = one(record)
    assert (interface.enabled, interface.oper_up, interface.state_reason) == (
        enabled,
        oper_up,
        reason,
    )


def test_admin_state_wins_over_the_reason() -> None:
    record = ETH | {"state": "down", "state_rsn_desc": "Administratively down"}
    assert one(record).enabled is True  # admin_state "up" is what the device says


def test_only_up_is_oper_up() -> None:
    assert one(ETH | {"state": "down"}).oper_up is False
    assert one(ETH | {"state": "sfp-not-inserted"}).oper_up is False


def test_single_interface_is_an_object_not_a_list() -> None:
    assert one(ETH).name == "Ethernet1/1"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"TABLE_interface": {}}, id="no-interfaces"),
        pytest.param({"TABLE_interface": {"ROW_interface": [ETH, ETH]}}, id="duplicate"),
        pytest.param(
            {"TABLE_interface": {"ROW_interface": {"interface": "nve1", "x": 1}}},
            id="unknown-field-set",
        ),
        pytest.param(
            {"TABLE_interface": {"ROW_interface": ETH | {"admin_state": "maybe"}}}, id="admin"
        ),
        pytest.param({"TABLE_interface": {"ROW_interface": ETH | {"eth_mtu": "big"}}}, id="mtu"),
        pytest.param({"TABLE_interface": {"ROW_interface": ETH | {"eth_hw_addr": "x"}}}, id="mac"),
        pytest.param({"TABLE_interface": {"ROW_interface": ETH | {"interface": ""}}}, id="no-name"),
    ],
)
def test_problems_are_parse_errors(data: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        parse_show_interface(json.dumps(data))


def test_unknown_field_set_error_lists_field_names_only() -> None:
    record = {"interface": "nve1", "nve_state": "up", "secret_value": "10.0.0.1"}
    with pytest.raises(ParseError) as excinfo:
        one(record)
    assert "nve_state, secret_value" in str(excinfo.value)
    assert "10.0.0.1" not in str(excinfo.value)


# -- device ------------------------------------------------------------------


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_interfaces_sends_only_show_interface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # One line wrapped at 511 columns and paged every 23 lines, as 10.5 sends it.
    one_line = json.dumps(
        json.loads((NAPALM / "ssh_Nexus3548_show_interface_json.txt").read_text())
    )
    lines = [one_line[i : i + 511] for i in range(0, len(one_line), 511)]
    pages = ["".join(f"{line}\r\n" for line in lines[i : i + 23]) for i in range(0, len(lines), 23)]
    assert len(pages) > 2
    conn = FakeCLI({SHOW_INTERFACE: pages}, hostname="sw1", terminator="# ").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsInterfaces)
        interfaces = device.get_interfaces()
    assert len(interfaces) == 62
    assert [w for w in conn.writes if w != " "] == [f"{SHOW_INTERFACE}\n", "exit\n"]
    assert conn.writes.count(" ") == len(pages) - 1
