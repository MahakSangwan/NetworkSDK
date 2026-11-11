import logging
import re
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import Path

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSNeighbor, IOSSSHTransport
from netsdk.cisco.ios.parsers import (
    parse_show_cdp_neighbors_detail,
    parse_show_lldp_neighbors_detail,
)
from netsdk.core import (
    CommandError,
    Credentials,
    NotSupportedError,
    ParseError,
    SupportsNeighbors,
)
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures"
CDP = FIXTURES / "show_cdp_neighbors_detail"
LLDP = FIXTURES / "show_lldp_neighbors_detail"

CREDS = Credentials(username="ro", password="pw")

CDP_TOTAL = "Total cdp entries displayed : {}"
LLDP_TOTAL = "Total entries displayed: {}"


def read(path: Path, total: str, marker: str) -> str:
    """A sample as the device prints it: some upstream ones were trimmed
    before the closing count line, which a device always prints."""
    text = path.read_text()
    if not re.search(r"^\s*Total .*entries displayed", text, re.MULTILINE):
        text += "\n" + total.format(len(re.findall(marker, text, re.MULTILINE))) + "\n"
    return text


def cdp(path: Path) -> str:
    return read(path, CDP_TOTAL, r"^Device ID:")


def lldp(path: Path) -> str:
    return read(path, LLDP_TOTAL, r"^Chassis id:")


# -- CDP (Genie) -------------------------------------------------------------


def test_cdp_nx_os_neighbor() -> None:
    neighbors = parse_show_cdp_neighbors_detail(
        cdp(CDP / "genieparser" / "ios_device_output_1.txt")
    )
    assert len(neighbors) == 3
    assert neighbors[0] == IOSNeighbor(
        protocol="cdp",
        local_interface="GigabitEthernet0/0",
        remote_name="R6(9P57K4EJ8CA)",  # NX-OS: serial in brackets, kept as sent
        remote_port="mgmt0",
        remote_platform="N9K-9000v",
        remote_description="Cisco Nexus Operating System (NX-OS) Software, Version 9.2(1)",
        management_addresses=(ip_address("172.16.1.203"),),
        entry_addresses=(ip_address("172.16.1.203"),),
        capabilities=("router", "switch", "cvta", "phone-port"),
        duplex="full",
    )
    assert neighbors[2].remote_name == "R5.cisco.com"
    assert neighbors[2].capabilities == ("router", "source-route-bridge")
    assert neighbors[2].remote_platform == "Cisco"


def test_cdp_two_links_to_one_neighbor_are_two_records() -> None:
    # The sample ntc-templates loses the second Spine-01 link on (D35).
    neighbors = parse_show_cdp_neighbors_detail(
        cdp(CDP / "genieparser" / "iosxe_device_output_11.txt")
    )
    assert [(n.remote_name, n.local_interface, n.remote_port) for n in neighbors] == [
        ("gsgsw-labswitch20", "GigabitEthernet0/0", "GigabitEthernet1/36"),
        ("ROUTER-VCG4", "GigabitEthernet1/0/5", "TenGigabitEthernet1/0/5"),
        ("Spine-01", "GigabitEthernet1/0/33", "TenGigabitEthernet1/0/33"),
        ("Spine-01", "GigabitEthernet1/0/34", "TenGigabitEthernet1/0/34"),
        ("DVTEP1-HOST1", "GigabitEthernet1/0/1", "TenGigabitEthernet1/0/1"),
    ]
    # Old IOS prints its version on the second line; the whole text is kept.
    first = neighbors[0].remote_description
    assert first is not None
    assert "Version 12.2(20)EWA4" in first.splitlines()[1]


def _samples(directory: Path, pattern: str) -> list[Path]:
    return sorted(directory.glob(f"*/{pattern}"))


#: Upstream sanitised an address to "XXXXXXXXXXXXx"; a device never prints that.
CDP_SANITISED = CDP / "genieparser" / "ios_device_output_10.txt"


def test_unreadable_cdp_address_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="unreadable neighbor address"):
        parse_show_cdp_neighbors_detail(cdp(CDP_SANITISED))


@pytest.mark.parametrize(
    "sample", [p for p in _samples(CDP, "*.*[tw]") if p != CDP_SANITISED], ids=lambda p: p.name
)
def test_every_cdp_sample(sample: Path) -> None:
    text = cdp(sample)
    neighbors = parse_show_cdp_neighbors_detail(text)
    names = re.findall(r"^Device ID: ?(.*?)\s*$", text, re.MULTILINE)
    assert [n.remote_name for n in neighbors] == names
    assert all(n.protocol == "cdp" and n.local_interface for n in neighbors)


# -- LLDP (ntc-templates) ----------------------------------------------------


def test_lldp_non_cisco_neighbors_keep_their_description() -> None:
    # Genie returns these descriptions empty (D35).
    text = lldp(LLDP / "ntc_templates" / "cisco_ios_show_lldp_neighbors_detail1.raw")
    juniper, hp, _ = parse_show_lldp_neighbors_detail(text)
    assert juniper == IOSNeighbor(
        protocol="lldp",
        local_interface="GigabitEthernet1/0/2",
        remote_name=None,  # "System Name - not advertised"
        remote_port="502",
        remote_port_description="ge-0/0/0.0",
        remote_chassis_id="7c25.86c9.aaaa",
        remote_description=(
            "Juniper Networks, Inc. ex2200-24t-4g , version 12.3R9.4 "
            "Build date: 2015-02-12 11:25:30 UTC"
        ),
        capabilities=("bridge", "router"),
        native_vlan=1,
    )
    assert hp.remote_name == "HpSwitch"
    assert hp.remote_port == "94f1.2879.5555"
    assert hp.remote_port_description == "13"
    assert hp.management_addresses == (ip_address("192.168.1.81"),)
    assert hp.capabilities == ("bridge",)
    assert hp.remote_description is not None
    assert hp.remote_description.startswith("HPE OfficeConnect Switch 1820 48G J9981A")


def test_lldp_ports_are_kept_as_advertised() -> None:
    # Genie expands "Gi1/0/4" and capitalises "c81f.77ff.dddd" (D35).
    text = lldp(LLDP / "genieparser" / "ios_golden_output_4.txt")
    assert parse_show_lldp_neighbors_detail(text) == [
        IOSNeighbor(
            protocol="lldp",
            local_interface="GigabitEthernet1/0/17",
            remote_name="TestName",
            remote_port="c81f.77ff.dddd",
            remote_chassis_id="127.0.0.2",
            management_addresses=(ip_address("127.0.0.2"),),
            capabilities=("bridge", "telephone"),
        )
    ]
    cisco = parse_show_lldp_neighbors_detail(lldp(LLDP / "genieparser" / "ios_golden_output.txt"))
    assert [n.remote_port for n in cisco] == ["Gi1/0/4", "Gi1/0/2", "Gi1/0/3", "Gi1/0/1"]


def test_lldp_without_local_interface_is_kept_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Some IOS releases print no "Local Intf:" line in detail output.
    text = lldp(LLDP / "ntc_templates" / "cisco_ios_show_lldp_neighbors_detail2.raw")
    with caplog.at_level(logging.WARNING):
        neighbors = parse_show_lldp_neighbors_detail(text)
    assert len(neighbors) == 4
    assert {n.local_interface for n in neighbors} == {None}
    assert "doesn't print its local interface" in caplog.text


@pytest.mark.parametrize("sample", _samples(LLDP, "*.*[tw]"), ids=lambda p: p.name)
def test_every_lldp_sample(sample: Path) -> None:
    text = lldp(sample)
    neighbors = parse_show_lldp_neighbors_detail(text)
    chassis = re.findall(r"^Chassis id: ?(.*?)\s*$", text, re.MULTILINE)
    assert [n.remote_chassis_id for n in neighbors] == chassis
    assert all(n.protocol == "lldp" for n in neighbors)


# -- both ----------------------------------------------------------------------

PARSERS: list[tuple[str, Callable[[str], list[IOSNeighbor]], str]] = [
    ("cdp", parse_show_cdp_neighbors_detail, CDP_TOTAL),
    ("lldp", parse_show_lldp_neighbors_detail, LLDP_TOTAL),
]


@pytest.mark.parametrize(("name", "parse", "total"), PARSERS, ids=[p[0] for p in PARSERS])
def test_no_neighbors_disabled_or_unknown_is_empty(
    name: str, parse: Callable[[str], list[IOSNeighbor]], total: str
) -> None:
    assert parse("\n" + total.format(0) + "\n") == []
    assert parse(f"% {name.upper()} is not enabled\n") == []
    assert parse("% Invalid input detected at '^' marker.\n") == []


@pytest.mark.parametrize(("name", "parse", "total"), PARSERS, ids=[p[0] for p in PARSERS])
def test_missing_or_wrong_count_is_a_parse_error(
    name: str, parse: Callable[[str], list[IOSNeighbor]], total: str
) -> None:
    sample = {
        "cdp": CDP / "genieparser" / "ios_device_output_1.txt",
        "lldp": LLDP / "genieparser" / "ios_golden_output.txt",
    }[name]
    text = sample.read_text()
    head = re.split(r"^\s*Total .*entries displayed.*$", text, flags=re.MULTILINE)[0]
    with pytest.raises(ParseError, match="cut short"):
        parse(head)
    with pytest.raises(ParseError, match="lists 9 neighbors"):
        parse(head + "\n" + total.format(9) + "\n")


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def device_output(text: str) -> str:
    return text.replace("\n", "\r\n").rstrip("\r\n") + "\r\n"


CDP_OUTPUT = device_output(cdp(CDP / "genieparser" / "ios_device_output_1.txt"))
LLDP_OUTPUT = device_output(lldp(LLDP / "genieparser" / "ios_golden_output_4.txt"))


def test_get_neighbors_sends_both_commands_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI(
        {"show cdp neighbors detail": [CDP_OUTPUT], "show lldp neighbors detail": [LLDP_OUTPUT]},
        hostname="sw1",
    ).connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsNeighbors)
        neighbors = device.get_neighbors()
    assert [n.protocol for n in neighbors] == ["cdp", "cdp", "cdp", "lldp"]
    assert conn.writes == ["show cdp neighbors detail\n", "show lldp neighbors detail\n", "exit\n"]


def test_disabled_protocol_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI(
        {
            "show cdp neighbors detail": ["% CDP is not enabled\r\n"],
            "show lldp neighbors detail": [LLDP_OUTPUT],
        },
        hostname="sw1",
    ).connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert [n.protocol for n in device.get_neighbors()] == ["lldp"]


def test_unknown_lldp_command_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show cdp neighbors detail": [CDP_OUTPUT]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        raw = device.get_neighbors_raw()
        neighbors = device.get_neighbors()
    assert "% Invalid input" in raw["show lldp neighbors detail"]
    assert {n.protocol for n in neighbors} == {"cdp"}


def test_neither_protocol_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = FakeCLI({}, hostname="r1").connection()  # both -> % Invalid input
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError) as excinfo,
    ):
        device.get_neighbors()
    assert excinfo.value.host == "sw1.example"


def test_authorization_failure_stays_a_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    denied = "Command authorization failed.\r\n"
    conn = FakeCLI({"show cdp neighbors detail": [denied]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_neighbors()
    assert not isinstance(excinfo.value, NotSupportedError)
