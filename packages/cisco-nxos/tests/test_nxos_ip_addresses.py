import json
from ipaddress import IPv4Interface
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_IP_INTERFACE
from netsdk.cisco.nxos.parsers import parse_show_ip_interface
from netsdk.core import Credentials, IPAddress, ParseError, SupportsIPAddresses
from netsdk.core.testing import FakeCLI, FakeConnection

NAPALM = Path(__file__).parent / "fixtures" / "napalm"
CREDS = Credentials(username="ro", password="pw")


def output(*pairs: tuple[str, dict[str, Any]]) -> str:
    """`show ip interface vrf all | json` as NX-OS lays it out: parallel lists."""
    return json.dumps(
        {
            "TABLE_vrf": [{"ROW_vrf": {"vrf-name-out": vrf}} for vrf, _ in pairs],
            "TABLE_intf": [{"ROW_intf": row} for _, row in pairs],
        }
    )


def intf(
    name: str, prefix: str | None = None, masklen: str | None = None, **extra: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {"intf-name": name, "proto-state": "up", "admin-state": "up"}
    if prefix is not None:
        row |= {"prefix": prefix, "subnet": "x", "masklen": masklen}
    return row | extra


def test_napalm_sample() -> None:
    text = (NAPALM / "nxapi_ipv6_n9k_show_ip_interface.json").read_text()
    addresses = parse_show_ip_interface(text)
    assert len(addresses) == 6
    assert addresses[0] == IPAddress(
        interface="loopback100", address=IPv4Interface("10.0.0.100/24"), vrf=None
    )
    assert addresses[-1].interface == "Ethernet2/5"


def test_vrfs_default_is_global() -> None:
    addresses = parse_show_ip_interface(
        output(
            ("management", intf("mgmt0", "192.0.2.10", "24")),
            ("default", intf("Vlan10", "10.0.10.1", "24")),
            ("BLUE", intf("Vlan20", "10.0.20.1", "24")),
            ("BLUE", intf("loopback1", "10.255.0.1", "32")),
        )
    )
    assert [(a.interface, a.vrf) for a in addresses] == [
        ("mgmt0", "management"),
        ("Vlan10", None),
        ("Vlan20", "BLUE"),
        ("loopback1", "BLUE"),
    ]
    assert addresses[-1].address == IPv4Interface("10.255.0.1/32")


@pytest.mark.parametrize("as_list", [True, False], ids=["several", "one-as-object"])
def test_secondaries_follow_the_primary(as_list: bool) -> None:
    secondaries = [
        {"prefix1": "10.0.11.1", "masklen1": "24"},
        {"prefix1": "10.0.12.1", "masklen1": "25"},
    ]
    table = {"ROW_secondary_address": secondaries if as_list else secondaries[0]}
    addresses = parse_show_ip_interface(
        output(("default", intf("Vlan10", "10.0.10.1", "24", TABLE_secondary_address=table)))
    )
    expected = [("10.0.10.1/24", False), ("10.0.11.1/24", True), ("10.0.12.1/25", True)]
    assert [(str(a.address), a.secondary) for a in addresses] == expected[: 3 if as_list else 2]


def test_unnumbered_and_addressless_interfaces_have_no_record() -> None:
    addresses = parse_show_ip_interface(
        output(
            ("default", intf("loopback0", "10.255.0.1", "32")),
            # Unnumbered, even if a release also printed the borrowed address.
            ("default", intf("Ethernet1/2", "10.255.0.1", "32", **{"unnum-intf": "loopback0"})),
            ("default", intf("Ethernet1/3")),
        )
    )
    assert [a.interface for a in addresses] == ["loopback0"]


def test_single_interface_entry_as_object() -> None:
    data = {
        "TABLE_vrf": {"ROW_vrf": {"vrf-name-out": "management"}},
        "TABLE_intf": {"ROW_intf": intf("mgmt0", "192.0.2.10", "24")},
    }
    (address,) = parse_show_ip_interface(json.dumps(data))
    assert (address.interface, address.vrf) == ("mgmt0", "management")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            json.dumps(
                {
                    "TABLE_vrf": [{"ROW_vrf": {"vrf-name-out": "default"}}],
                    "TABLE_intf": [{"ROW_intf": intf("a", "10.0.0.1", "24")}] * 2,
                }
            ),
            id="lists-differ-in-length",
        ),
        pytest.param(output(("default", intf("Vlan10", "10.0.10.1", None))), id="no-masklen"),
        pytest.param(output(("default", intf("Vlan10", "10.0.10.1", "33"))), id="bad-masklen"),
        pytest.param(output(("default", intf("Vlan10", "10.0.10", "24"))), id="bad-address"),
        pytest.param(
            output(("default", intf("Vlan10", "10.0.10.1", "24")), ("BLUE", intf("Vlan10"))),
            id="interface-twice",
        ),
        pytest.param(
            output(
                (
                    "default",
                    intf(
                        "Vlan10",
                        "10.0.10.1",
                        "24",
                        TABLE_secondary_address={"ROW_secondary_address": {"prefix1": "10.0.11.1"}},
                    ),
                )
            ),
            id="secondary-without-masklen",
        ),
        pytest.param(output(("", intf("Vlan10", "10.0.10.1", "24"))), id="no-vrf-name"),
    ],
)
def test_problems_are_parse_errors(text: str) -> None:
    with pytest.raises(ParseError):
        parse_show_ip_interface(text)


# -- device ------------------------------------------------------------------


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_ip_addresses_sends_only_the_vrf_all_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = output(
        ("management", intf("mgmt0", "192.0.2.10", "24")),
        ("default", intf("Vlan10", "10.0.10.1", "24")),
    )
    conn = FakeCLI(
        {SHOW_IP_INTERFACE: [text + "\r\n"]}, hostname="sw1", terminator="# "
    ).connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsIPAddresses)
        addresses = device.get_ip_addresses()
    assert [a.vrf for a in addresses] == ["management", None]
    assert conn.writes == [f"{SHOW_IP_INTERFACE}\n", "exit\n"]
