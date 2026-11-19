import json
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import NXOSVPC, CiscoNXOS, NXOSSSHTransport, NXOSVPCPeerLink
from netsdk.cisco.nxos.commands import SHOW_VPC
from netsdk.cisco.nxos.parsers import parse_show_vpc
from netsdk.core import Credentials, NotSupportedError, ParseError
from netsdk.core.testing import FakeConnection

CREDS = Credentials(username="ro", password="pw")


def vpc_row(
    vpc_id: int, name: str, state: str = "1", consistency: str = "consistent"
) -> dict[str, Any]:
    return {
        "vpc-id": str(vpc_id),
        "vpc-ifindex": name,
        "vpc-port-state": state,
        "phy-port-if-removed": "disabled",
        "vpc-thru-peerlink": "0",
        "vpc-consistency": consistency,
        "vpc-consistency-status": "SUCCESS",
        "up-vlan-bitset": "1,10-20",
        "es-attr": "DF: Invalid",
    }


#: The fields 9.3/10.5 print (live layout probe, 2026-11-19); values invented.
VPC_93: dict[str, Any] = {
    "vpc-domain-id": "10",
    "vpc-hdr": "Legend",
    "vpc-peer-status": "peer-ok",
    "vpc-peer-status-reason": "SUCCESS",
    "vpc-peer-keepalive-status": "peer-alive",
    "vpc-peer-consistency": "consistent",
    "vpc-per-vlan-peer-consistency": "consistent",
    "vpc-peer-consistency-status": "SUCCESS",
    "vpc-type-2-consistency": "consistent",
    "vpc-type-2-consistency-status": "SUCCESS",
    "vpc-role": "primary",
    "num-of-vpcs": "2",
    "peer-gateway": "1",
    "dual-active-excluded-vlans": "-",
    "vpc-graceful-consistency-check-status": "enabled",
    "vpc-auto-recovery-status": "Disabled",
    "vpc-delay-restore-status": "Timer is off.(timeout = 30s)",
    "virtual-peerlink": "Disabled",
    "vpc-peer-link-hdr": "Start of VPC peer-link table",
    "TABLE_peerlink": {
        "ROW_peerlink": {
            "peer-link-id": "1",
            "peerlink-ifindex": "Po1",
            "peer-link-port-state": "1",
            "peer-up-vlan-bitset": "1-100",
        }
    },
    "vpc-end": ["End of table", "End of table"],
    "TABLE_vpc": {"ROW_vpc": [vpc_row(20, "Po20"), vpc_row(21, "Po21", "0", "not-applicable")]},
}


def test_93_pair_member() -> None:
    vpc = parse_show_vpc(json.dumps(VPC_93))
    assert (vpc.domain_id, vpc.role) == (10, "primary")
    assert (vpc.peer_ok, vpc.keepalive_alive) == (True, True)
    assert (vpc.peer_consistent, vpc.type2_consistent, vpc.per_vlan_consistent) == (
        True,
        True,
        True,
    )
    assert (vpc.graceful_consistency_check, vpc.auto_recovery, vpc.peer_gateway) == (
        True,
        "Disabled",
        "1",
    )
    assert vpc.peer_link == NXOSVPCPeerLink(port_channel="port-channel1", link_id=1, up=True)
    assert vpc.vpcs == (
        NXOSVPC(
            vpc_id=20,
            port_channel="port-channel20",
            up=True,
            consistent=True,
            consistency="consistent",
            consistency_reason="SUCCESS",
        ),
        NXOSVPC(
            vpc_id=21,
            port_channel="port-channel21",
            up=False,
            consistent=None,  # not-applicable
            consistency="not-applicable",
            consistency_reason="SUCCESS",
        ),
    )
    assert "up-vlan-bitset" not in json.dumps(vpc.model_dump())  # operational detail left out


def test_73_chunked_table_and_word_states() -> None:
    # 7.3(11)N1(1): TABLE_vpc is a list of chunks; port states are words.
    data = VPC_93 | {
        "TABLE_peerlink": {
            "ROW_peerlink": {
                "peer-link-id": "1",
                "peerlink-ifindex": "Po1",
                "peer-link-port-state": "enabled",
            }
        },
        "TABLE_vpc": [
            {"ROW_vpc": [vpc_row(n, f"Po{n}", "disabled") for n in range(1, 101)]},
            {"ROW_vpc": [vpc_row(n, f"Po{n}", "enabled") for n in range(101, 151)]},
        ],
        "peer-gateway": "Enabled",
        "vpc-auto-recovery-status": "Enabled, timer is off.(timeout = 240s)",
    }
    vpc = parse_show_vpc(json.dumps(data))
    assert len(vpc.vpcs) == 150
    assert [v.vpc_id for v in vpc.vpcs[98:102]] == [99, 100, 101, 102]
    assert (vpc.vpcs[0].up, vpc.vpcs[-1].up) == (False, True)
    assert vpc.peer_link is not None
    assert vpc.peer_link.up is True


def test_unhealthy_pair() -> None:
    data = VPC_93 | {
        "vpc-peer-status": "peer-not-alive",
        "vpc-peer-status-reason": "Peer was not reachable",
        "vpc-peer-keepalive-status": "peer-not-alive",
        "vpc-type-2-consistency": "inconsistent",
        "vpc-type-2-consistency-status": "SVI type-2 configuration incompatible",
        "vpc-role": "primary-secondary",
    }
    vpc = parse_show_vpc(json.dumps(data))
    assert (vpc.peer_ok, vpc.keepalive_alive, vpc.type2_consistent) == (False, False, False)
    assert (vpc.peer_status, vpc.keepalive_status) == ("peer-not-alive", "peer-not-alive")
    assert vpc.role == "primary-secondary"


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"vpc-domain-id": "not configured"}, id="no-domain"),
        pytest.param({"vpc-peer-status": None}, id="no-peer-status"),
        pytest.param({"vpc-role": None}, id="no-role"),
        pytest.param({"vpc-peer-consistency": "maybe"}, id="consistency"),
        pytest.param({"TABLE_vpc": {"ROW_vpc": vpc_row(1, "Po1", "sideways")}}, id="port-state"),
        pytest.param({"TABLE_vpc": {"ROW_vpc": vpc_row(1, "")}}, id="no-port-channel"),
    ],
)
def test_problems_are_parse_errors(changes: dict[str, Any]) -> None:
    data = {k: v for k, v in (VPC_93 | changes).items() if v is not None}
    with pytest.raises(ParseError):
        parse_show_vpc(json.dumps(data))


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


def test_get_vpc_status_sends_only_show_vpc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = switch(json.dumps(VPC_93) + "\r\n")
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert device.get_vpc_status().domain_id == 10
    assert conn.writes == [f"{SHOW_VPC}\n", "exit\n"]


def test_vpc_off_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = switch("                 ^\r\n% Invalid command at '^' marker.\r\n")
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError, match="vPC"),
    ):
        device.get_vpc_status()
