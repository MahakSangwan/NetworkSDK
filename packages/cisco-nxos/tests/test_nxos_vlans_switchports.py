import json
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import NXOSVLAN, CiscoNXOS, NXOSSSHTransport, NXOSSwitchport
from netsdk.cisco.nxos.commands import SHOW_INTERFACE_SWITCHPORT, SHOW_VLAN_BRIEF
from netsdk.cisco.nxos.parsers import (
    parse_show_interface_switchport,
    parse_show_vlan_brief,
    vlan_ids,
)
from netsdk.core import Credentials, ParseError, SupportsSwitchports, SupportsVLANs
from netsdk.core.testing import FakeCLI, FakeConnection

NAPALM = Path(__file__).parent / "fixtures" / "napalm"
CREDS = Credentials(username="ro", password="pw")


def port(name: str, mode: str = "access", access: str = "1", **extra: str) -> dict[str, str]:
    """A `show interface switchport | json` row with the fields seen live (2026-11-16)."""
    return {
        "interface": name,
        "switchport": "Enabled",
        "switchport_monitor": "Not enabled",
        "oper_mode": mode,
        "access_vlan": access,
        "access_vlan_name": "x",
        "native_vlan": "1",
        "native_vlan_name": "default",
        "trunk_vlans": "1-4094",
        "voice_vlan_name": "none",
    } | extra


def switchport_json(*records: dict[str, str]) -> str:
    row: Any = records[0] if len(records) == 1 else list(records)
    return json.dumps({"TABLE_interface": {"ROW_interface": row}})


# -- switchports --------------------------------------------------------------


def test_access_and_trunk_ports() -> None:
    ports = parse_show_interface_switchport(
        switchport_json(
            port("Ethernet1/1", access="10"),
            port("port-channel1", mode="trunk", native_vlan="99", trunk_vlans="10-12,20"),
            port("Ethernet1/2", mode="trunk"),
        )
    )
    assert ports == [
        NXOSSwitchport(
            interface="Ethernet1/1",
            mode="access",
            access_vlan=10,
            native_vlan=1,
            all_vlans_allowed=True,
            oper_mode="access",
        ),
        NXOSSwitchport(
            interface="port-channel1",
            mode="trunk",
            access_vlan=1,
            native_vlan=99,
            allowed_vlans=(10, 11, 12, 20),
            oper_mode="trunk",
        ),
        NXOSSwitchport(
            interface="Ethernet1/2",
            mode="trunk",
            access_vlan=1,
            native_vlan=1,
            all_vlans_allowed=True,  # "1-4094" (D41)
            oper_mode="trunk",
        ),
    ]


def test_other_modes_are_kept_as_printed() -> None:
    (fex,) = parse_show_interface_switchport(
        switchport_json(port("Ethernet1/49", mode="fex-fabric"))
    )
    assert (fex.mode, fex.oper_mode) == (None, "fex-fabric")


def test_routed_ports_have_no_record() -> None:
    ports = parse_show_interface_switchport(
        switchport_json(port("Ethernet1/1"), port("Ethernet1/2", switchport="Disabled"))
    )
    assert [p.interface for p in ports] == ["Ethernet1/1"]


@pytest.mark.parametrize(
    ("trunk_vlans", "all_allowed", "allowed"),
    [
        ("1-4094", True, None),
        ("none", False, ()),
        ("1", False, (1,)),
        ("1-3, 5", False, (1, 2, 3, 5)),
        ("2-4094", False, tuple(range(2, 4095))),
    ],
)
def test_allowed_vlans(
    trunk_vlans: str, all_allowed: bool, allowed: tuple[int, ...] | None
) -> None:
    (p,) = parse_show_interface_switchport(
        switchport_json(port("Ethernet1/1", mode="trunk", trunk_vlans=trunk_vlans))
    )
    assert (p.all_vlans_allowed, p.allowed_vlans) == (all_allowed, allowed)


@pytest.mark.parametrize("value", ["1-", "a-b", "0-5", "5-3", "1-4095", "1,,2"])
def test_vlan_ids_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="not a VLAN list"):
        vlan_ids(value)


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(port("Ethernet1/1", switchport="maybe"), id="switchport-state"),
        pytest.param(port("Ethernet1/1", access_vlan="ten"), id="access-vlan"),
        pytest.param(port("Ethernet1/1", trunk_vlans="lots"), id="allowed"),
        pytest.param(
            port(
                "",
            ),
            id="no-name",
        ),
    ],
)
def test_switchport_problems_are_parse_errors(record: dict[str, str]) -> None:
    with pytest.raises(ParseError):
        parse_show_interface_switchport(switchport_json(record))


def test_duplicate_switchport_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_show_interface_switchport(switchport_json(port("Ethernet1/1"), port("Ethernet1/1")))


# -- VLANs ---------------------------------------------------------------------


def test_trunk_ports_are_filtered_out() -> None:
    # Live NX-OS lists trunk ports under each VLAN; only access ports stay (D41).
    vlans = {
        v.vlan_id: v
        for v in parse_show_vlan_brief(
            (NAPALM / "ssh_normal_show_vlan_brief_json.txt").read_text(),
            switchport_json(
                port("Ethernet1/15", access="1"),
                port("Ethernet1/21", access="177"),
                port("Ethernet1/22", mode="trunk"),
                port("port-channel12", mode="trunk"),
            ),
        )
    }
    assert len(vlans) == 32
    assert vlans[1] == NXOSVLAN(
        vlan_id=1,
        name="default",
        active=True,
        interfaces=("Ethernet1/15",),
        status="active",
        shut_state="noshutdown",
    )
    assert vlans[177].interfaces == ("Ethernet1/21",)
    assert vlans[161].interfaces == ()


def test_port_ranges_and_list_form() -> None:
    vlans = parse_show_vlan_brief(
        (NAPALM / "ssh_6000_show_vlan_brief_json.txt").read_text(),
        switchport_json(
            port("Ethernet1/8", access="230"),
            port("port-channel11", access="230"),
            port("Ethernet101/1/9", access="231"),  # FEX port, inside "Ethernet101/1/4-14"
            port("Ethernet1/19", access="231"),  # inside "Ethernet1/18-20"
        ),
    )
    assert [(v.vlan_id, v.name, v.interfaces) for v in vlans] == [
        (230, "database", ("port-channel11", "Ethernet1/8")),
        (231, "APPLICATION", ("Ethernet1/19", "Ethernet101/1/9")),
    ]


def test_utf_vlan_id_wins_over_a_garbled_plain_one() -> None:
    vlans = parse_show_vlan_brief(
        (NAPALM / "ssh_issue_1490_utf_show_vlan_brief_json.txt").read_text(),
        switchport_json(port("Ethernet1/15", access="1")),
    )
    assert [v.vlan_id for v in vlans][:3] == [1, 161, 177]


def vlan_json(**row: Any) -> str:
    base = {
        "vlanshowbr-vlanid": "10",
        "vlanshowbr-vlanid-utf": "10",
        "vlanshowbr-vlanname": "users",
        "vlanshowbr-vlanstate": "active",
        "vlanshowbr-shutstate": "noshutdown",
    }
    return json.dumps({"TABLE_vlanbriefxbrief": {"ROW_vlanbriefxbrief": base | row}})


@pytest.mark.parametrize(
    ("state", "shut", "active"),
    [
        ("active", "noshutdown", True),
        ("active", "shutdown", False),
        ("suspend", "noshutdown", False),
    ],
)
def test_active_needs_active_and_not_shut(state: str, shut: str, active: bool) -> None:
    (vlan,) = parse_show_vlan_brief(
        vlan_json(**{"vlanshowbr-vlanstate": state, "vlanshowbr-shutstate": shut}),
        switchport_json(port("Ethernet1/1")),
    )
    assert (vlan.active, vlan.status, vlan.shut_state) == (active, state, shut)


def test_vlan_without_ports() -> None:
    (vlan,) = parse_show_vlan_brief(vlan_json(), switchport_json(port("Ethernet1/1")))
    assert vlan.interfaces == ()


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"vlanshowbr-vlanid-utf": "x", "vlanshowbr-vlanid": "x"}, id="id"),
        pytest.param({"vlanshowbr-vlanid-utf": "4095"}, id="id-range"),
        pytest.param({"vlanshowbr-vlanstate": None}, id="no-state"),
        pytest.param({"vlanshowplist-ifidx": 5}, id="ports-not-text"),
        pytest.param({"vlanshowplist-ifidx": "Ethernet1/9-3"}, id="backwards-range"),
    ],
)
def test_vlan_problems_are_parse_errors(row: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        parse_show_vlan_brief(vlan_json(**row), switchport_json(port("Ethernet1/1")))


# -- device ------------------------------------------------------------------


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_getters_send_only_their_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outputs = {
        SHOW_VLAN_BRIEF: [vlan_json(**{"vlanshowplist-ifidx": "Ethernet1/1,Ethernet1/2"}) + "\r\n"],
        SHOW_INTERFACE_SWITCHPORT: [
            switchport_json(port("Ethernet1/1", access="10"), port("Ethernet1/2", mode="trunk"))
            + "\r\n"
        ],
    }
    conn = FakeCLI(outputs, hostname="sw1", terminator="# ").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsVLANs)
        assert isinstance(device, SupportsSwitchports)
        (vlan,) = device.get_vlans()
        ports = device.get_switchports()
    assert vlan.interfaces == ("Ethernet1/1",)
    assert [p.mode for p in ports] == ["access", "trunk"]
    assert conn.writes == [
        f"{SHOW_VLAN_BRIEF}\n",
        f"{SHOW_INTERFACE_SWITCHPORT}\n",
        f"{SHOW_INTERFACE_SWITCHPORT}\n",
        "exit\n",
    ]
