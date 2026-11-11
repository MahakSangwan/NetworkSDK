import re
from pathlib import Path

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport, IOSSwitchport
from netsdk.cisco.ios.names import full_interface_name
from netsdk.cisco.ios.parsers import parse_show_interfaces_switchport
from netsdk.core import (
    CommandError,
    Credentials,
    NotSupportedError,
    ParseError,
    SupportsSwitchports,
)
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures" / "show_interfaces_switchport"
GENIE = FIXTURES / "genieparser"
NTC = FIXTURES / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# Hand-written for what the upstream samples lack: a routed port, a "NONE"
# allowed list, a 100G port, a voice VLAN, a port that prints only its mode.
SHOW_INTERFACES_SWITCHPORT = """\
Name: Gi1/0/1
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Administrative Trunking Encapsulation: negotiate
Operational Trunking Encapsulation: native
Negotiation of Trunking: Off
Access Mode VLAN: 10 (USERS)
Trunking Native Mode VLAN: 1 (default)
Administrative Native VLAN tagging: enabled
Voice VLAN: 20 (VOICE)
Administrative private-vlan host-association: none
Administrative private-vlan mapping: none
Administrative private-vlan trunk native VLAN: none
Administrative private-vlan trunk Native VLAN tagging: enabled
Administrative private-vlan trunk encapsulation: dot1q
Administrative private-vlan trunk normal VLANs: none
Administrative private-vlan trunk associations: none
Administrative private-vlan trunk mappings: none
Operational private-vlan: none
Trunking VLANs Enabled: ALL
Pruning VLANs Enabled: 2-1001
Capture Mode Disabled
Capture VLANs Allowed: ALL

Protected: false
Unknown unicast blocked: disabled
Unknown multicast blocked: disabled
Appliance trust: none

Name: Hu1/0/49
Switchport: Enabled
Administrative Mode: trunk
Operational Mode: trunk
Administrative Trunking Encapsulation: dot1q
Operational Trunking Encapsulation: dot1q
Negotiation of Trunking: On
Access Mode VLAN: 1 (default)
Trunking Native Mode VLAN: 99 (NATIVE)
Administrative Native VLAN tagging: enabled
Voice VLAN: none
Administrative private-vlan host-association: none
Administrative private-vlan mapping: none
Administrative private-vlan trunk native VLAN: none
Administrative private-vlan trunk Native VLAN tagging: enabled
Administrative private-vlan trunk encapsulation: dot1q
Administrative private-vlan trunk normal VLANs: none
Administrative private-vlan trunk associations: none
Administrative private-vlan trunk mappings: none
Operational private-vlan: none
Trunking VLANs Enabled: NONE
Pruning VLANs Enabled: 2-1001
Capture Mode Disabled
Capture VLANs Allowed: ALL

Protected: false
Unknown unicast blocked: disabled
Unknown multicast blocked: disabled
Appliance trust: none

Name: Gi1/0/2
Switchport: Disabled

Name: Gi1/1/1
Switchport: Enabled
Administrative Mode: dynamic auto

"""


def test_hand_written_sample() -> None:
    ports = {p.interface: p for p in parse_show_interfaces_switchport(SHOW_INTERFACES_SWITCHPORT)}
    # The routed port has no record.
    assert list(ports) == ["GigabitEthernet1/0/1", "HundredGigE1/0/49", "GigabitEthernet1/1/1"]
    assert ports["GigabitEthernet1/0/1"] == IOSSwitchport(
        interface="GigabitEthernet1/0/1",
        mode="access",
        access_vlan=10,
        native_vlan=1,
        all_vlans_allowed=True,
        allowed_vlans=None,
        admin_mode="static access",
        oper_mode="static access",
        voice_vlan=20,
        negotiation=False,
        encapsulation="native",
    )
    assert ports["HundredGigE1/0/49"] == IOSSwitchport(
        interface="HundredGigE1/0/49",
        mode="trunk",
        access_vlan=1,
        native_vlan=99,
        all_vlans_allowed=False,
        allowed_vlans=(),  # "NONE"
        admin_mode="trunk",
        oper_mode="trunk",
        negotiation=True,
        encapsulation="dot1q",
    )
    # Prints only its admin mode (e.g. an empty uplink slot): the rest is
    # unknown, not "none".
    assert ports["GigabitEthernet1/1/1"] == IOSSwitchport(
        interface="GigabitEthernet1/1/1", mode=None, admin_mode="dynamic auto"
    )


def test_genieparser_sample_with_bundle_and_wrapped_vlan_list() -> None:
    ports = {
        p.interface: p
        for p in parse_show_interfaces_switchport((GENIE / "ios_golden_output.txt").read_text())
    }
    # Genie's order, not the device's: a bundle member brings its port-channel forward.
    assert sorted(ports) == [
        "GigabitEthernet1/0/2",
        "GigabitEthernet1/0/4",
        "GigabitEthernet1/0/5",
        "Port-channel12",
    ]
    # "trunk (member of bundle Po12)"
    assert ports["GigabitEthernet1/0/2"] == IOSSwitchport(
        interface="GigabitEthernet1/0/2",
        mode="trunk",
        access_vlan=1,
        native_vlan=1,
        allowed_vlans=tuple(range(100, 111)),
        admin_mode="trunk",
        oper_mode="trunk",
        negotiation=True,
        encapsulation="dot1q",
    )
    assert ports["GigabitEthernet1/0/5"] == IOSSwitchport(
        interface="GigabitEthernet1/0/5",
        mode=None,  # down
        access_vlan=1,
        native_vlan=1,
        all_vlans_allowed=True,
        admin_mode="static access",
        oper_mode="down",
        negotiation=False,
    )
    # Down port-channel: native VLAN printed as "0 (Inactive)"; the allowed
    # list wraps over three lines.
    wrapped = [
        *(100, 101, *range(110, 121), 121, 130, 170, 180),  # 100,101,110-120,121,130,170,180,
        *(*range(333, 356), *range(366, 401), 540, *range(601, 606), 606, 607),  # 333-355,...
        *(1111, 1113, *range(1200, 1235), *range(1400, 1457), 1567),  # 1111,1113,1200-1234,...
    ]
    assert ports["Port-channel12"] == IOSSwitchport(
        interface="Port-channel12",
        mode=None,
        access_vlan=100,
        native_vlan=None,
        allowed_vlans=tuple(wrapped),
        admin_mode="trunk",
        oper_mode="down",
        voice_vlan=100,
        negotiation=True,
    )


def test_ntc_templates_voice_vlan_sample() -> None:
    text = (NTC / "cisco_ios_show_interfaces_switchport_voice_vlan.raw").read_text()
    ports = parse_show_interfaces_switchport(text)
    assert [(p.interface, p.mode, p.access_vlan, p.admin_mode) for p in ports] == [
        ("GigabitEthernet0/1", "trunk", 1, "trunk"),
        ("GigabitEthernet0/2", "access", 100, "dynamic auto"),
        ("GigabitEthernet0/3", "access", 101, "dynamic auto"),
        ("GigabitEthernet1/0", "access", 110, "dynamic auto"),
        ("GigabitEthernet1/1", "access", 111, "dynamic auto"),
    ]
    assert all(p.all_vlans_allowed and p.voice_vlan is None for p in ports)


def _samples() -> list[Path]:
    single_interface = GENIE / "iosxe_golden_output_3.txt"
    return sorted(p for p in [*GENIE.glob("*.txt"), *NTC.glob("*.raw")] if p != single_interface)


@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.name)
def test_every_upstream_sample(sample: Path) -> None:
    text = sample.read_text()
    blocks = re.findall(r"^Name:\s*(\S+)\nSwitchport: (\w+)", text, re.MULTILINE)
    ports = parse_show_interfaces_switchport(text)
    # One record per enabled switchport, in any order, with full names.
    assert sorted(p.interface for p in ports) == sorted(
        full_interface_name(name) for name, state in blocks if state == "Enabled"
    )
    for port in ports:
        assert full_interface_name(port.interface) == port.interface
        expected = {"static access": "access", "trunk": "trunk"}.get(port.oper_mode or "")
        assert port.mode == expected


def test_single_interface_output_with_a_bundle_member_is_a_parse_error() -> None:
    # `show interfaces Gi1/1 switchport` on a port-channel member: Genie wants
    # the port-channel's own block, which only the full command prints. The
    # getter always sends the full command.
    with pytest.raises(ParseError, match="Port-channel17"):
        parse_show_interfaces_switchport((GENIE / "iosxe_golden_output_3.txt").read_text())


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("", id="empty"),
        pytest.param("junk\nlines\n", id="junk"),
    ],
)
def test_unparseable_output_is_a_parse_error(output: str) -> None:
    with pytest.raises(ParseError):
        parse_show_interfaces_switchport(output)


def test_unreadable_allowed_vlans_are_a_parse_error() -> None:
    text = SHOW_INTERFACES_SWITCHPORT.replace(
        "Trunking VLANs Enabled: NONE", "Trunking VLANs Enabled: 1-5000"
    )
    with pytest.raises(ParseError, match="HundredGigE1/0/49"):
        parse_show_interfaces_switchport(text)


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_switchports_sends_only_show_interfaces_switchport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = SHOW_INTERFACES_SWITCHPORT.replace("\n", "\r\n")
    conn = FakeCLI({"show interfaces switchport": [output]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsSwitchports)
        ports = device.get_switchports()
    assert len(ports) == 3
    assert conn.writes == ["show interfaces switchport\n", "exit\n"]


def test_router_without_switchports_is_not_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({}, hostname="r1").connection()  # unknown command -> % Invalid input
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError) as excinfo,
    ):
        device.get_switchports()
    assert excinfo.value.host == "sw1.example"


def test_authorization_failure_stays_a_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    denied = "Command authorization failed.\r\n"
    conn = FakeCLI({"show interfaces switchport": [denied]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_switchports()
    assert not isinstance(excinfo.value, NotSupportedError)
