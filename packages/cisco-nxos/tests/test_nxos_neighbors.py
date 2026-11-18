import json
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSNeighbor, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_CDP_NEIGHBORS_DETAIL, SHOW_LLDP_NEIGHBORS_DETAIL
from netsdk.cisco.nxos.parsers import (
    parse_show_cdp_neighbors_detail,
    parse_show_lldp_neighbors_detail,
    parse_show_vtp_status,
)
from netsdk.core import (
    CommandError,
    Credentials,
    NotSupportedError,
    ParseError,
    SupportsNeighbors,
)
from netsdk.core.testing import FakeConnection

CREDS = Credentials(username="ro", password="pw")

# Fields seen live (layout probe, 2026-11-18); values invented.
CDP_ROW: dict[str, Any] = {
    "ifindex": "436207616",
    "device_id": "core1(FDO12345678)",
    "sysname": "core1",
    "numaddr": "1",
    "v4addr": "10.0.0.2",
    "platform_id": "N9K-C93180YC-EX",
    "capability": ["router", "switch", "IGMP_cnd_filtering", "Supports-STP-Dispute"],
    "intf_id": "Ethernet1/49",
    "port_id": "Ethernet1/49",
    "ttl": "150",
    "version": "Cisco Nexus Operating System (NX-OS) Software, Version 9.3(9)",
    "version_no": "v2",
    "nativevlan": "1",
    "duplexmode": "full",
    "mtu": "1500",
    "syslocation": "lab",
    "num_mgmtaddr": "1",
    "v4mgmtaddr": "10.0.0.2",
    "vtpname": "CORP",
}
LLDP_ROW: dict[str, Any] = {
    "chassis_type": "4",
    "chassis_id": "2cc2.6017.d760",
    "port_type": "5",
    "port_id": "Ethernet1/50",
    "l_port_id": "Eth1/50",
    "port_desc": "uplink",
    "ttl": "120",
    "sys_name": "core2",
    "sys_desc": "Cisco Nexus Operating System (NX-OS) Software 9.3(9)",
    "system_capability": "B, R",
    "enabled_capability": "B",
    "mgmt_addr_type": "IPV4",
    "mgmt_addr": "10.0.0.3",
    "vlan_id": "1",
}


def cdp(*rows: dict[str, Any]) -> str:
    row: Any = rows[0] if len(rows) == 1 else list(rows)
    return json.dumps({"TABLE_cdp_neighbor_detail_info": {"ROW_cdp_neighbor_detail_info": row}})


def lldp(*rows: dict[str, Any], count: int | None = None) -> str:
    row: Any = rows[0] if len(rows) == 1 else list(rows)
    data: dict[str, Any] = {"neigh_hdr": "neigh_hdr", "TABLE_nbor_detail": {"ROW_nbor_detail": row}}
    if count is not None:
        data["neigh_count"] = str(count)
    return json.dumps(data)


def test_cdp_neighbor() -> None:
    (n,) = parse_show_cdp_neighbors_detail(cdp(CDP_ROW))
    assert n == NXOSNeighbor(
        protocol="cdp",
        local_interface="Ethernet1/49",
        remote_name="core1(FDO12345678)",  # as advertised (D35)
        remote_port="Ethernet1/49",
        remote_platform="N9K-C93180YC-EX",
        remote_description=CDP_ROW["version"],
        management_addresses=(ip_address("10.0.0.2"),),
        capabilities=("router", "switch", "igmp_cnd_filtering", "supports-stp-dispute"),
        entry_addresses=(ip_address("10.0.0.2"),),
        native_vlan=1,
        duplex="full",
        remote_system_name="core1",
    )


def test_two_cdp_links_to_the_same_neighbor_are_two_records() -> None:
    neighbors = parse_show_cdp_neighbors_detail(
        cdp(CDP_ROW, CDP_ROW | {"intf_id": "Ethernet1/50", "port_id": "Ethernet1/49"})
    )
    assert [(n.local_interface, n.remote_port) for n in neighbors] == [
        ("Ethernet1/49", "Ethernet1/49"),
        ("Ethernet1/50", "Ethernet1/49"),
    ]


def test_lldp_neighbor() -> None:
    (n,) = parse_show_lldp_neighbors_detail(lldp(LLDP_ROW, count=1))
    assert n == NXOSNeighbor(
        protocol="lldp",
        local_interface="Ethernet1/50",  # "Eth1/50" expanded to join Interface.name
        remote_name="core2",
        remote_port="Ethernet1/50",
        remote_port_description="uplink",
        remote_chassis_id="2cc2.6017.d760",  # as advertised, not normalized
        remote_description=LLDP_ROW["sys_desc"],
        management_addresses=(ip_address("10.0.0.3"),),
        capabilities=("bridge",),  # enabled, not system, capabilities
        native_vlan=1,
    )


@pytest.mark.parametrize(
    ("changes", "addresses", "vlan"),
    [
        ({"mgmt_addr": "not advertised", "vlan_id": None}, (), None),
        ({"mgmt_addr": "00:11:22:33:44:55", "mgmt_addr_type": "MAC"}, (), 1),
        (
            {"mgmt_addr": ["10.0.0.3", "fe80::1"]},
            (ip_address("10.0.0.3"), ip_address("fe80::1")),
            1,
        ),
    ],
)
def test_lldp_optional_values(
    changes: dict[str, Any], addresses: tuple[Any, ...], vlan: int | None
) -> None:
    row = {k: v for k, v in (LLDP_ROW | changes).items() if v is not None}
    (n,) = parse_show_lldp_neighbors_detail(lldp(row))
    assert (n.management_addresses, n.native_vlan) == (addresses, vlan)


def test_lldp_count_must_match() -> None:
    with pytest.raises(ParseError, match="says 2 neighbors but lists 1"):
        parse_show_lldp_neighbors_detail(lldp(LLDP_ROW, count=2))


def test_unknown_lldp_code_is_logged_and_kept(caplog: pytest.LogCaptureFixture) -> None:
    (n,) = parse_show_lldp_neighbors_detail(lldp(LLDP_ROW | {"enabled_capability": "B, X"}))
    assert n.capabilities == ("bridge", "x")
    assert "unrecognised LLDP capability code 'X'" in caplog.text


@pytest.mark.parametrize(
    "parse", [parse_show_cdp_neighbors_detail, parse_show_lldp_neighbors_detail]
)
def test_empty_output_is_no_neighbors(parse: Any) -> None:
    assert parse("") == []
    assert parse("\n  \n") == []


def test_neighbor_without_local_interface_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_show_cdp_neighbors_detail(cdp({k: v for k, v in CDP_ROW.items() if k != "intf_id"}))


def test_vtp_updater_interface() -> None:
    # 7.3(11)N1(1) prints updater_interface instead of updater_id (live, 2026-11-18).
    vtp = {
        "running-version": "1",
        "oper_mode": "transparent",
        "config_rev": "0",
        "num_current_vlans": "185",
        "pruning_mode": "disabled",
        "trap_enabled": "disabled",
        "updater_interface": "Vlan10",
        "local_updater_format": "1",
        "vtp_file": "bootflash:/vlan.dat",
    }
    assert parse_show_vtp_status(json.dumps(vtp)).updater_interface == "Vlan10"


# -- device ------------------------------------------------------------------


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


REJECTED = "                 ^\r\n% Invalid command at '^' marker.\r\n"


def switch(replies: dict[str, str]) -> FakeConnection:
    def responder(data: str) -> list[str]:
        command = data.rstrip("\n")
        if command == "exit":
            conn.remote_conn.closed = True
            return ["exit\r\n"]
        return [f"{command}\r\n", replies[command], "sw1# "]

    conn = FakeConnection(responder, ["sw1# "])
    return conn


def test_get_neighbors_cdp_then_lldp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = switch(
        {
            SHOW_CDP_NEIGHBORS_DETAIL: cdp(CDP_ROW) + "\r\n",
            SHOW_LLDP_NEIGHBORS_DETAIL: lldp(LLDP_ROW, count=1) + "\r\n",
        }
    )
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsNeighbors)
        neighbors = device.get_neighbors()
    assert [n.protocol for n in neighbors] == ["cdp", "lldp"]
    assert conn.writes == [
        f"{SHOW_CDP_NEIGHBORS_DETAIL}\n",
        f"{SHOW_LLDP_NEIGHBORS_DETAIL}\n",
        "exit\n",
    ]


def test_lldp_off_gives_cdp_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = switch(
        {SHOW_CDP_NEIGHBORS_DETAIL: cdp(CDP_ROW) + "\r\n", SHOW_LLDP_NEIGHBORS_DETAIL: REJECTED}
    )
    with caplog.at_level("INFO"), make_device(monkeypatch, tmp_path, conn) as device:
        neighbors = device.get_neighbors()
        raw = device.get_neighbors_raw()
    assert [n.protocol for n in neighbors] == ["cdp"]
    assert "% Invalid command" in raw[SHOW_LLDP_NEIGHBORS_DETAIL]
    assert "that protocol is off" in caplog.text


def test_both_off_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = switch({SHOW_CDP_NEIGHBORS_DETAIL: REJECTED, SHOW_LLDP_NEIGHBORS_DETAIL: REJECTED})
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(NotSupportedError):
        device.get_neighbors()


def test_authorization_failure_stays_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = switch(
        {
            SHOW_CDP_NEIGHBORS_DETAIL: "% Permission denied for the role\r\n",
            SHOW_LLDP_NEIGHBORS_DETAIL: lldp(LLDP_ROW) + "\r\n",
        }
    )
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_neighbors()
    assert not isinstance(excinfo.value, NotSupportedError)
