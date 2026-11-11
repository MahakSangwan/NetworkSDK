from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport, IOSVTPStatus
from netsdk.cisco.ios.parsers import parse_show_vtp_status
from netsdk.core import CommandError, Credentials, NotSupportedError, ParseError
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures" / "show_vtp_status"
GENIE = FIXTURES / "genieparser"
NTC = FIXTURES / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# Expected values are written by hand from the raw samples, not copied from
# Genie's own expected results: a Genie upgrade that misreads them must fail here.
# Only fields that are set are listed; everything else must be None.
EXPECTED: dict[str, dict[str, Any]] = {
    # Oldest flat layout (12.2-era).
    "ios_golden_output.txt": {
        "version": 2,
        "domain_name": "Lab_Network",
        "mode": "client",
        "configuration_revision": 247,
        "vlan_count": 33,
        "max_vlans": 1005,
        "pruning_enabled": True,
        "traps_enabled": False,
        "last_modified_at": "8-12-99 15:04:49",  # by 0.0.0.0
    },
    "iosxe_golden_output_2.txt": {
        "version": 1,
        "max_version": 2,  # "running VTP1 (VTP2 capable)"
        "mode": "transparent",
        "configuration_revision": 0,
        "vlan_count": 100,
        "max_vlans": 1005,
        "pruning_enabled": True,
        "traps_enabled": True,
        # Domain "<>", modified by 0.0.0.0 at 0-0-00 00:00:00: all None.
    },
    # v1 on a v3-capable switch: header plus a flat "Feature VLAN" block.
    "iosxe_golden_output.txt": {
        "version": 1,
        "max_version": 3,
        "mode": "server",
        "configuration_revision": 55,
        "vlan_count": 53,
        "max_vlans": 1005,
        "pruning_enabled": False,
        "traps_enabled": False,
        "device_id": "38:20:56:ff:c7:a2",
        "last_modified_by": IPv4Address("192.168.234.1"),
        "last_modified_at": "12-5-17 09:35:46",
        "updater_id": IPv4Address("192.168.234.1"),
        "updater_interface": "Vlan100",
    },
    # v3: VLAN, MST and UNKNOWN features; only VLAN is read.
    "ios_golden_output_3.txt": {
        "version": 3,
        "max_version": 3,
        "mode": "primary server",
        "configuration_revision": 2,
        "vlan_count": 100,
        "extended_vlan_count": 0,
        "max_vlans": 4096,
        "pruning_enabled": False,
        "traps_enabled": False,
        "device_id": "38:20:56:ff:c7:a2",
        "primary_id": "38:20:56:ff:c7:a2",
        "primary_description": "SW1",
    },
    "ios_golden_output_4.txt": {
        "version": 3,
        "max_version": 3,
        "domain_name": "GENIE",
        "mode": "client",
        "configuration_revision": 15,
        "vlan_count": 47,
        "extended_vlan_count": 0,
        "max_vlans": 2048,
        "pruning_enabled": False,
        "traps_enabled": False,
        "device_id": "88:5a:92:ff:7c:92",
        "primary_id": "50:1c:bf:ff:a9:1e",
        "primary_description": "sw001",
    },
    "iosxe_golden_output_4.txt": {
        "version": 3,
        "max_version": 3,
        "domain_name": "GENIE",
        "mode": "primary server",
        "configuration_revision": 25,
        "vlan_count": 40,
        "extended_vlan_count": 0,
        "max_vlans": 2048,
        "pruning_enabled": False,
        "traps_enabled": False,
        "device_id": "02:da:30:ff:a8:78",
        "primary_id": "02:da:30:ff:a8:78",
        "primary_description": "genie",
    },
    # v3 client: no "Maximum VLANs" line, extended VLANs in use.
    "iosxe_golden_output_5.txt": {
        "version": 3,
        "max_version": 3,
        "domain_name": "Domain-Name",
        "mode": "client",
        "configuration_revision": 13,
        "vlan_count": 40,
        "extended_vlan_count": 2,
        "pruning_enabled": True,
        "traps_enabled": True,
        "device_id": "ff:ff:aa:ff:aa:aa",
        "primary_id": "33:33:11:ff:33:33",
        "primary_description": "description",
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_genieparser_samples(name: str) -> None:
    status = parse_show_vtp_status((GENIE / name).read_text())
    assert status == IOSVTPStatus(**EXPECTED[name])


def test_iosxe_golden_output_3_is_the_ios_sample() -> None:
    # Same output filed under both OSes upstream; keep it covered, not duplicated.
    text = (GENIE / "iosxe_golden_output_3.txt").read_text()
    assert parse_show_vtp_status(text) == IOSVTPStatus(**EXPECTED["ios_golden_output_3.txt"])


def test_every_sample_is_covered() -> None:
    samples = {p.name for p in GENIE.glob("*.txt")}
    assert samples == {*EXPECTED, "iosxe_golden_output_3.txt"}


def test_ntc_templates_sample() -> None:
    status = parse_show_vtp_status((NTC / "cisco_ios_show_vtp_status.raw").read_text())
    assert status == IOSVTPStatus(
        version=1,
        max_version=3,
        domain_name="router_vtp",
        mode="server",
        configuration_revision=38,
        vlan_count=20,
        max_vlans=1005,
        pruning_enabled=False,
        traps_enabled=False,
        device_id="e0:5f:de:ad:be:ef",
        last_modified_by=IPv4Address("10.0.0.1"),
        last_modified_at="6-12-15 14:06:57",
        updater_id=IPv4Address("10.1.1.1"),
        updater_interface="Vlan1",
    )


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("", id="empty"),
        pytest.param("% Invalid input detected at '^' marker.\n", id="rejected"),
        pytest.param("junk\nlines\n", id="junk"),
        pytest.param("VTP Pruning Mode : Enabled\n", id="stray-line"),  # crashes inside Genie
    ],
)
def test_unparseable_output_is_a_parse_error(output: str) -> None:
    with pytest.raises(ParseError):
        parse_show_vtp_status(output)


@pytest.mark.parametrize("lines", [3, 10])
def test_truncated_output_is_a_parse_error(lines: int) -> None:
    for name in ("ios_golden_output.txt", "ios_golden_output_3.txt"):
        text = "\n".join((GENIE / name).read_text().splitlines()[:lines])
        with pytest.raises(ParseError):
            parse_show_vtp_status(text)


def test_unknown_mode_is_a_parse_error() -> None:
    text = (GENIE / "ios_golden_output.txt").read_text().replace(": Client", ": Sideways")
    with pytest.raises(ParseError, match="unexpected 'show vtp status' value"):
        parse_show_vtp_status(text)


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_vtp_status_sends_only_show_vtp_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The upstream file has no final newline; a device ends output with one.
    output = (GENIE / "ios_golden_output_4.txt").read_text().replace("\n", "\r\n") + "\r\n"
    conn = FakeCLI({"show vtp status": [output]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        status = device.get_vtp_status()
        raw = device.get_vtp_status_raw()
    assert status == IOSVTPStatus(**EXPECTED["ios_golden_output_4.txt"])
    assert list(raw) == ["show vtp status"]
    assert conn.writes == ["show vtp status\n", "show vtp status\n", "exit\n"]


def test_parse_error_names_the_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = FakeCLI({"show vtp status": ["junk\r\n"]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(ParseError) as excinfo:
        device.get_vtp_status()
    assert excinfo.value.host == "sw1.example"


def test_router_without_vtp_is_not_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({}, hostname="r1").connection()  # unknown command -> % Invalid input
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError) as excinfo,
    ):
        device.get_vtp_status()
    assert excinfo.value.host == "sw1.example"


def test_authorization_failure_stays_a_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    denied = "Command authorization failed.\r\n"
    conn = FakeCLI({"show vtp status": [denied]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_vtp_status()
    assert not isinstance(excinfo.value, NotSupportedError)
