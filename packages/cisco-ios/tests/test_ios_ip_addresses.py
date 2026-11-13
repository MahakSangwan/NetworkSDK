from ipaddress import IPv4Interface, IPv6Interface
from pathlib import Path

import pytest
from pydantic import ValidationError

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport
from netsdk.cisco.ios.parsers import parse_show_ip_interface
from netsdk.core import Credentials, IPAddress, ParseError, SupportsIPAddresses
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures" / "show_ip_interface"
UPSTREAM = FIXTURES / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# Hand-written: primary + secondary, a VRF interface, an unnumbered tunnel and
# an interface without IP.
SHOW_IP_INTERFACE = (
    "Vlan10 is up, line protocol is up\r\n"
    "  Internet address is 10.10.0.1/24\r\n"
    "  Broadcast address is 255.255.255.255\r\n"
    "  Secondary address 10.10.1.1/24\r\n"
    "  MTU is 1500 bytes\r\n"
    "GigabitEthernet0/0 is up, line protocol is up\r\n"
    "  Internet address is 192.0.2.10/28\r\n"
    "  MTU is 1500 bytes\r\n"
    '  VPN Routing/Forwarding "MGMT"\r\n'
    "Tunnel0 is up, line protocol is up\r\n"
    "  Interface is unnumbered. Using address of Vlan10 (10.10.0.1)\r\n"
    "GigabitEthernet1/0/1 is up, line protocol is up\r\n"
    "  Internet protocol processing disabled\r\n"
)


def read(name: str) -> str:
    return (UPSTREAM / f"{name}.raw").read_text()


def test_hand_written_sample() -> None:
    assert parse_show_ip_interface(SHOW_IP_INTERFACE) == [
        IPAddress(interface="Vlan10", address=IPv4Interface("10.10.0.1/24")),
        IPAddress(interface="Vlan10", address=IPv4Interface("10.10.1.1/24"), secondary=True),
        IPAddress(
            interface="GigabitEthernet0/0", address=IPv4Interface("192.0.2.10/28"), vrf="MGMT"
        ),
    ]


@pytest.mark.parametrize(
    ("name", "count", "secondaries", "in_a_vrf"),
    [
        # 5 tunnels print only a transport VRF ("Tunnel VPN ..."): global (D37).
        ("cisco_ios_show_ip_interface", 58, 0, 28),
        ("cisco_ios_show_ip_interface2", 6, 1, 0),
        ("cisco_ios_show_ip_interface3", 1, 0, 0),
        ("cisco_ios_show_ip_interface4", 2, 0, 0),
        ("cisco_ios_show_ip_interface5", 0, 0, 0),  # address still to be negotiated
        ("cisco_ios_show_ip_interface6", 2, 0, 1),
        ("cisco_ios_show_ip_interface7", 3, 0, 0),
        ("cisco_ios_show_ip_interface8", 1, 0, 0),
    ],
)
def test_upstream_samples(name: str, count: int, secondaries: int, in_a_vrf: int) -> None:
    addresses = parse_show_ip_interface(read(name))
    assert len(addresses) == count
    assert sum(a.secondary for a in addresses) == secondaries
    assert sum(a.vrf is not None for a in addresses) == in_a_vrf


def test_all_upstream_samples_are_covered() -> None:
    assert len(list(UPSTREAM.glob("*.raw"))) == 8


def test_upstream_values_match_the_raw_text() -> None:
    # Spot checks read off the .raw files by hand.
    sample2 = parse_show_ip_interface(read("cisco_ios_show_ip_interface2"))
    vlan1 = [a for a in sample2 if a.interface == "Vlan1"]
    assert [(str(a.address), a.secondary) for a in vlan1] == [
        ("192.168.1.1/24", False),
        ("192.168.1.3/24", True),
    ]
    # Tunnel0 is unnumbered (borrows Loopback0's address): no record of its own.
    assert "Tunnel0" not in {a.interface for a in sample2}
    assert [str(a.address) for a in sample2 if a.interface == "Loopback0"] == ["10.255.255.1/32"]

    sample1 = parse_show_ip_interface(read("cisco_ios_show_ip_interface"))
    mgmt = next(a for a in sample1 if a.interface == "GigabitEthernet0")
    assert (str(mgmt.address), mgmt.vrf) == ("172.21.2.3/24", "Mgmt-intf")
    # 'Tunnel VPN Routing/Forwarding "MYCOMPANY"' is the transport VRF; the
    # tunnel's own address is in the global table.
    tunnels = {a.interface: a for a in sample1 if a.interface.startswith("Tunnel")}
    assert sorted(tunnels) == ["Tunnel5001", "Tunnel5002", "Tunnel5010", "Tunnel6001", "Tunnel6002"]
    assert {a.vrf for a in tunnels.values()} == {None}
    assert str(tunnels["Tunnel5001"].address) == "169.254.64.0/31"


def test_tunnel_transport_vrf_is_not_the_interface_vrf() -> None:
    text = (FIXTURES / "netsdk" / "cisco_ios_show_ip_interface_tunnel_vrf.raw").read_text()
    assert [(a.interface, str(a.address), a.vrf) for a in parse_show_ip_interface(text)] == [
        ("GigabitEthernet0/0/0", "198.51.100.1/30", "UNDERLAY"),
        ("Tunnel100", "192.0.2.1/24", None),  # global, tunnel vrf UNDERLAY
        ("Tunnel200", "192.0.2.129/25", "OVERLAY"),  # tunnel vrf UNDERLAY
    ]


def test_released_template_still_reads_the_transport_vrf() -> None:
    # Why the corrected copy exists. When this fails, a released ntc-templates
    # has networktocode/ntc-templates#2378: delete the copy (D37).
    from ntc_templates.parse import parse_output

    rows = parse_output(
        platform="cisco_ios", command="show ip interface", data=read("cisco_ios_show_ip_interface")
    )
    assert next(r for r in rows if r["interface"] == "Tunnel5001")["vrf"] == "MYCOMPANY"


@pytest.mark.parametrize(
    "line", ["Internet address is 10.10.0.1", "Internet address is 10.10.0.1/"]
)
def test_address_without_prefix_is_a_parse_error(line: str) -> None:
    # Never guessed as /32.
    output = f"Vlan10 is up, line protocol is up\r\n  {line}\r\n"
    with pytest.raises(ParseError, match="expected address/length"):
        parse_show_ip_interface(output)


def test_model_takes_ipv6_for_later() -> None:
    address = IPAddress(interface="Vlan10", address="2001:db8::1/64")
    assert address.address == IPv6Interface("2001:db8::1/64")
    assert address.model_dump(mode="json")["address"] == "2001:db8::1/64"
    with pytest.raises(ValidationError):
        IPAddress(interface="Vlan10", address="10.0.0.300/24")


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_ip_addresses_sends_only_show_ip_interface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show ip interface": [SHOW_IP_INTERFACE]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsIPAddresses)
        addresses = device.get_ip_addresses()
    assert len(addresses) == 3
    assert conn.writes == ["show ip interface\n", "exit\n"]
