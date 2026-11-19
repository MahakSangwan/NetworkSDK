import json
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import NXOSFEX, CiscoNXOS, NXOSFEXFabricPort, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_FEX_DETAIL
from netsdk.cisco.nxos.parsers import parse_show_fex_detail
from netsdk.core import Credentials, NotSupportedError, ParseError
from netsdk.core.testing import FakeConnection

CREDS = Credentials(username="ro", password="pw")


def fex(fex_id: int, state: str = "Online", ports: int = 3, **extra: Any) -> dict[str, Any]:
    """A ROW_fex_info with the fields seen live (layout probe, 2026-11-19); values invented."""
    return {
        "chas_id": str(fex_id),
        "descr": f'"FEX0{fex_id}"',
        "fex_state": state,
        "fex_ver": "7.3(11)N1(1)",
        "fex_interim_ver": "7.3(11)N1(1)",
        "fex_sw_gen": "21",
        "sw_interim_ver": "7.3(11)N1(1)",
        "sw_sw_gen": "21",
        "post_level": "complete",
        "pin_mode": "static",
        "max_link": "1",
        "fbr_port_control": f"Po{fex_id}",
        "mac": "00:11:22:33:44:55",
        "num_macs": "64",
        "model": "N2K-C2248TP-1GE",
        "serial": f"SSI0000{fex_id}",
        "part_no": "73-13232-01",
        "card_id": "99",
        "TABLE_fbr_state": {
            "ROW_fbr_state": [
                {"fbr_index": "Eth1/1", "fbr_oper_state": "Up", "fsm_state": "Active"},
                {"fbr_index": "Eth1/2", "fbr_oper_state": "Down", "fsm_state": "Configured"},
            ]
        },
        "TABLE_fex_port": {
            "ROW_fex_port": [{"fex_port": f"Eth{fex_id}/1/{n}"} for n in range(1, ports + 1)]
        },
        "TABLE_logs": {"ROW_logs": [{"log": "Module register received"}]},
    } | extra


def detail(*rows: dict[str, Any]) -> str:
    row: Any = rows[0] if len(rows) == 1 else list(rows)
    return json.dumps({"TABLE_fex_info": {"ROW_fex_info": row}})


def test_online_fex() -> None:
    (f,) = parse_show_fex_detail(detail(fex(101)))
    assert f == NXOSFEX(
        fex_id=101,
        description="FEX0101",  # quotes dropped (D38)
        model="N2K-C2248TP-1GE",
        part_id="73-13232-01",
        serial_number="SSI0000101",
        software_version="7.3(11)N1(1)",
        fabric_port_channel="port-channel101",
        fabric_interfaces=(
            NXOSFEXFabricPort(interface="Ethernet1/1", up=True, state="Active"),
            NXOSFEXFabricPort(interface="Ethernet1/2", up=False, state="Configured"),
        ),
        host_interfaces=("Ethernet101/1/1", "Ethernet101/1/2", "Ethernet101/1/3"),
        pinning_mode="static",
        max_links=1,
        online=True,
        state="Online",
    )


def test_two_fexes_and_one_not_online() -> None:
    fexes = parse_show_fex_detail(detail(fex(101), fex(102, state="Offline", ports=0)))
    assert [(f.fex_id, f.online, f.state, len(f.host_interfaces)) for f in fexes] == [
        (101, True, "Online", 3),
        (102, False, "Offline", 0),
    ]


def test_no_fexes() -> None:
    assert parse_show_fex_detail("") == []
    assert parse_show_fex_detail(json.dumps({})) == []


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"chas_id": "x"}, id="id"),
        pytest.param({"fex_state": None}, id="no-state"),
        pytest.param(
            {
                "TABLE_fbr_state": {
                    "ROW_fbr_state": {"fbr_index": "Eth1/1", "fbr_oper_state": "Sideways"}
                }
            },
            id="fabric-state",
        ),
        pytest.param({"TABLE_fex_port": {"ROW_fex_port": {"fex_port": ""}}}, id="host-port"),
    ],
)
def test_problems_are_parse_errors(changes: dict[str, Any]) -> None:
    row = {k: v for k, v in (fex(101) | changes).items() if v is not None}
    with pytest.raises(ParseError):
        parse_show_fex_detail(detail(row))


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def switch(reply: str) -> FakeConnection:
    def responder(data: str) -> list[str]:
        command = data.rstrip("\n")
        if command == "exit":
            conn.remote_conn.closed = True
            return ["exit\r\n"]
        return [f"{command}\r\n", reply, "sw1# "]

    conn = FakeConnection(responder, ["sw1# "])
    return conn


def test_get_fexes_sends_only_show_fex_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = switch(detail(fex(101)) + "\r\n")
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert [f.fex_id for f in device.get_fexes()] == [101]
    assert conn.writes == [f"{SHOW_FEX_DETAIL}\n", "exit\n"]


def test_fex_off_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = switch("                 ^\r\n% Invalid command at '^' marker.\r\n")
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError, match="FEX"),
    ):
        device.get_fexes()
