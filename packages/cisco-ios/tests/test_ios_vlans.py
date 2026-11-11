import logging
from pathlib import Path

import pytest
from netutils.interface import abbreviated_interface_name

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport
from netsdk.cisco.ios.models import IOSVLAN
from netsdk.cisco.ios.names import full_interface_name
from netsdk.cisco.ios.parsers import parse_show_interfaces, parse_show_vlan_brief
from netsdk.core import (
    CommandError,
    Credentials,
    NotSupportedError,
    ParseError,
    SupportsVLANs,
)
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures"
UPSTREAM = FIXTURES / "show_vlan" / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# Hand-written: wrapped port list, extended-range VLAN, 32-character name,
# shut and suspended VLANs, the legacy 1002-1005.
SHOW_VLAN_BRIEF = (
    "\r\n"
    "VLAN Name                             Status    Ports\r\n"
    "---- -------------------------------- --------- -------------------------------\r\n"
    "1    default                          active    Gi1/0/7, Gi1/0/12, Te1/1/3\r\n"
    "10   USERS                            active    Gi1/0/1, Gi1/0/2, Gi1/0/3, Gi1/0/4\r\n"
    "                                                Gi1/0/5, Twe1/0/1, Po24\r\n"
    "20   VOICE                            act/lshut \r\n"
    "30   a-name-that-is-32-characters-lon suspended \r\n"
    "2000 EXTENDED                         active    Tw1/0/48\r\n"
    "1002 fddi-default                     act/unsup \r\n"
    "1003 token-ring-default               act/unsup \r\n"
    "1004 fddinet-default                  act/unsup \r\n"
    "1005 trnet-default                    act/unsup \r\n"
)


def test_hand_written_sample() -> None:
    vlans = {v.vlan_id: v for v in parse_show_vlan_brief(SHOW_VLAN_BRIEF)}
    assert list(vlans) == [1, 10, 20, 30, 2000, 1002, 1003, 1004, 1005]
    assert vlans[10] == IOSVLAN(
        vlan_id=10,
        name="USERS",
        active=True,
        interfaces=(
            "GigabitEthernet1/0/1",
            "GigabitEthernet1/0/2",
            "GigabitEthernet1/0/3",
            "GigabitEthernet1/0/4",
            "GigabitEthernet1/0/5",
            "TwentyFiveGigE1/0/1",
            "Port-channel24",
        ),
        status="active",
    )
    assert vlans[2000].interfaces == ("TwoGigabitEthernet1/0/48",)
    assert vlans[30].name == "a-name-that-is-32-characters-lon"
    # Only exactly "active" is active: shut, suspended and unsupported are not.
    assert [(v.status, v.active) for v in (vlans[20], vlans[30], vlans[1002])] == [
        ("act/lshut", False),
        ("suspended", False),
        ("act/unsup", False),
    ]
    assert vlans[20].interfaces == ()


def test_upstream_show_vlan_sample() -> None:
    # Full `show vlan` output; only the first table is read.
    vlans = {
        v.vlan_id: v
        for v in parse_show_vlan_brief((UPSTREAM / "cisco_ios_show_vlan.raw").read_text())
    }
    assert list(vlans) == [1, 10, 50, 60, 1002, 1003, 1004, 1005]
    assert vlans[1].interfaces == ("GigabitEthernet0/1",)
    assert vlans[10].interfaces == ()
    assert vlans[50].interfaces == tuple(f"FastEthernet0/{i}" for i in range(1, 13))
    assert vlans[60].interfaces == tuple(f"FastEthernet0/{i}" for i in range(13, 25))


def test_duplicate_vlan_is_a_parse_error() -> None:
    output = SHOW_VLAN_BRIEF + "10   AGAIN                            active    \r\n"
    with pytest.raises(ParseError, match=r"VLAN \[10\] more than once"):
        parse_show_vlan_brief(output)


# -- interface names -------------------------------------------------------


def _full_names() -> list[str]:
    names: set[str] = set()
    for raw in (FIXTURES / "show_interfaces" / "ntc_templates").glob("*.raw"):
        try:
            names.update(i.name for i in parse_show_interfaces(raw.read_text()))
        except ParseError:
            continue  # the spliced sample
    return sorted(names)


def test_full_names_are_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    # Every name `show interfaces` prints must survive expansion untouched,
    # or getters wouldn't join (netutils spells Vlan as "VLAN").
    names = _full_names()
    assert len(names) > 50
    assert [n for n in names if full_interface_name(n) != n] == []
    assert caplog.records == []


def test_abbreviations_expand_back_to_full_names() -> None:
    assert [
        n for n in _full_names() if full_interface_name(abbreviated_interface_name(n)) != n
    ] == []


@pytest.mark.parametrize(
    ("short", "full"),
    [
        # netutils spells these differently or not at all; IOS-XE prints the right side.
        ("Hu1/0/49", "HundredGigE1/0/49"),
        ("Wl0/1/4", "Wlan-GigabitEthernet0/1/4"),
        ("Vl10", "Vlan10"),
    ],
)
def test_ios_spellings(short: str, full: str) -> None:
    assert full_interface_name(short) == full
    assert full_interface_name(full) == full


def test_unknown_prefix_is_logged_and_kept(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert full_interface_name("Xyz1/0/1") == "Xyz1/0/1"
    assert "Xyz1/0/1" in caplog.text


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_vlans_sends_only_show_vlan_brief(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show vlan brief": [SHOW_VLAN_BRIEF]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsVLANs)
        vlans = device.get_vlans()
    assert len(vlans) == 9
    assert conn.writes == ["show vlan brief\n", "exit\n"]


def test_router_without_vlans_is_not_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({}, hostname="r1").connection()  # unknown command -> % Invalid input
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError) as excinfo,
    ):
        device.get_vlans()
    assert excinfo.value.host == "sw1.example"


def test_authorization_failure_stays_a_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    denied = "Command authorization failed.\r\n"
    conn = FakeCLI({"show vlan brief": [denied]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_vlans()
    assert not isinstance(excinfo.value, NotSupportedError)
