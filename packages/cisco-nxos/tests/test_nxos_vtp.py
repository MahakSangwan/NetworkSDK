import json
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSSSHTransport, NXOSVTPStatus
from netsdk.cisco.nxos.commands import SHOW_VTP_STATUS
from netsdk.cisco.nxos.parsers import parse_show_vtp_status
from netsdk.core import CommandError, Credentials, NotSupportedError, ParseError
from netsdk.core.testing import FakeCLI, FakeConnection

CREDS = Credentials(username="ro", password="pw")

#: The fields 9.3 prints (live layout probe, 2026-11-18); values invented.
VTP_93: dict[str, Any] = {
    "running-version": "1",
    "max_vlan_supported_local": "1005",
    "num_current_vlans": "190",
    "oper_mode": "transparent",
    "domain_name": "CORP",
    "pruning_mode": "disabled",
    "oper_pruning_mode": "disabled",
    "v2_mode": "disabled",
    "trap_enabled": "disabled",
    "md5_digest": "0x9A 0x3C 0x00 0x11 0x22 0x33 0x44 0x55 0x66 0x77 0x88 0x99 0xAA 0xBB 0xCC 0xDD",
    "last_modified_ip": "0.0.0.0",
    "last_modified_time": "0-0-00 00:00:00",
    "updater_id": "0.0.0.0",
    "updater_reason": "no valid interface found",
    "config_rev": "0",
    "start": "1",
    "version": "1",
}


def dumps(**changes: Any) -> str:
    return json.dumps({k: v for k, v in (VTP_93 | changes).items() if v is not None})


def test_93_transparent() -> None:
    assert parse_show_vtp_status(dumps()) == NXOSVTPStatus(
        version=1,
        configured_version=1,
        domain_name="CORP",
        mode="transparent",
        configuration_revision=0,
        vlan_count=190,
        max_vlans=1005,
        pruning_enabled=False,
        oper_pruning_enabled=False,
        traps_enabled=False,
        v2_mode_enabled=False,
        md5_digest=VTP_93["md5_digest"],
        last_modified_by=None,  # 0.0.0.0
        last_modified_at="0-0-00 00:00:00",
        updater_id=None,  # 0.0.0.0
        updater_reason="no valid interface found",
    )


def test_server_with_updates() -> None:
    vtp = parse_show_vtp_status(
        dumps(
            oper_mode="server",
            config_rev=12,  # ints as well as strings
            pruning_mode="enabled",
            last_modified_ip="10.0.0.5",
            updater_id="10.0.0.1",
            **{"running-version": 2, "version": 2},
        )
    )
    assert (vtp.mode, vtp.version, vtp.configuration_revision) == ("server", 2, 12)
    assert vtp.pruning_enabled is True
    assert (vtp.last_modified_by, vtp.updater_id) == (
        IPv4Address("10.0.0.5"),
        IPv4Address("10.0.0.1"),
    )


def test_optional_fields_absent() -> None:
    vtp = parse_show_vtp_status(
        dumps(
            version=None,
            domain_name="",
            max_vlan_supported_local=None,
            oper_pruning_mode=None,
            v2_mode=None,
            md5_digest=None,
            last_modified_ip=None,
            last_modified_time=None,
            updater_id=None,
            updater_reason=None,
        )
    )
    assert vtp.domain_name is None
    assert vtp.model_dump(exclude_none=True).keys() == {
        "version",
        "mode",
        "configuration_revision",
        "vlan_count",
        "pruning_enabled",
        "traps_enabled",
    }


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"running-version": None}, id="no-version"),
        pytest.param({"running-version": "4"}, id="version-range"),
        pytest.param({"oper_mode": "primary"}, id="unknown-mode"),
        pytest.param({"oper_mode": None}, id="no-mode"),
        pytest.param({"config_rev": "x"}, id="revision"),
        pytest.param({"trap_enabled": "maybe"}, id="flag"),
        pytest.param({"pruning_mode": None}, id="no-pruning"),
        pytest.param({"updater_id": "on Vlan10"}, id="address"),
    ],
)
def test_problems_are_parse_errors(changes: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        parse_show_vtp_status(dumps(**changes))


def test_text_instead_of_json_is_a_parse_error_showing_it() -> None:
    # What a switch with VTP off prints is unknown (none on the bench): if it
    # isn't a rejected command, the error must at least show the text.
    with pytest.raises(ParseError, match="no JSON object; it starts: 'VTP feature is not enabled'"):
        parse_show_vtp_status("VTP feature is not enabled\n")


# -- device ------------------------------------------------------------------


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_vtp_status_sends_only_its_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI(
        {SHOW_VTP_STATUS: [dumps() + "\r\n"]}, hostname="sw1", terminator="# "
    ).connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert device.get_vtp_status().mode == "transparent"
    assert conn.writes == [f"{SHOW_VTP_STATUS}\n", "exit\n"]


def rejecting(reply: str) -> FakeConnection:
    def responder(data: str) -> list[str]:
        if data == f"{SHOW_VTP_STATUS}\n":
            return [f"{SHOW_VTP_STATUS}\r\n", reply, "sw1# "]
        if data == "exit\n":
            conn.remote_conn.closed = True
            return ["exit\r\n"]
        return ["\r\nsw1# "]

    conn = FakeConnection(responder, ["sw1# "])
    return conn


def test_vtp_off_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = rejecting("                 ^\r\n% Invalid command at '^' marker.\r\n")
    with (
        make_device(monkeypatch, tmp_path, conn) as device,
        pytest.raises(NotSupportedError, match="VTP is not enabled"),
    ):
        device.get_vtp_status()


def test_authorization_failure_stays_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = rejecting("% Permission denied for the role\r\n")
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(CommandError) as excinfo:
        device.get_vtp_status()
    assert not isinstance(excinfo.value, NotSupportedError)
