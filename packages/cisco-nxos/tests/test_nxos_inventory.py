import json
from pathlib import Path

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_INVENTORY
from netsdk.cisco.nxos.parsers import parse_show_inventory, unquote
from netsdk.core import Credentials, InventoryItem, ParseError, SupportsInventory
from netsdk.core.testing import FakeCLI, FakeConnection

NAPALM = Path(__file__).parent / "fixtures" / "napalm"
CREDS = Credentials(username="ro", password="pw")


def test_napalm_sample() -> None:
    items = parse_show_inventory((NAPALM / "nxapi_normal_show_inventory.json").read_text())
    assert items[0] == InventoryItem(
        name="Chassis",
        description="Nexus 6001 Chassis",
        part_id="N6K-C6001-64P",
        version_id="V01",
        serial_number="TM6017D760B",
    )
    fan = items[-1]
    assert (fan.name, fan.version_id, fan.serial_number) == ("Fan 1", None, None)  # "N/A"


def test_quotes_are_dropped() -> None:
    # 10.5 wraps names and descriptions in quotes (live, 2026-11-13; D38).
    items = parse_show_inventory((NAPALM / "ssh_N93180_show_inventory_json.txt").read_text())
    assert items[0].name == "Chassis"
    row = {
        "name": '"FEX 101 CHASSIS"',
        "desc": '"Fabric Extender 48x1GE"',
        "productid": "N2K-C2248TP-1GE",
    }
    (fex,) = parse_show_inventory(json.dumps({"TABLE_inv": {"ROW_inv": row}}))
    assert (fex.name, fex.description) == ("FEX 101 CHASSIS", "Fabric Extender 48x1GE")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"Chassis"', "Chassis"),
        ("Chassis", "Chassis"),
        ('"a "b" c"', 'a "b" c'),
        ('""', None),
        ('"', '"'),
    ],
)
def test_unquote(value: str, expected: str | None) -> None:
    assert unquote(value) == expected


def test_no_items_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_show_inventory(json.dumps({"TABLE_inv": {}}))


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_inventory_sends_only_show_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = (NAPALM / "nxapi_normal_show_inventory.json").read_text().replace("\n", "\r\n")
    conn = FakeCLI({SHOW_INVENTORY: [text]}, hostname="sw1", terminator="# ").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsInventory)
        assert len(device.get_inventory()) == 4
    assert conn.writes == [f"{SHOW_INVENTORY}\n", "exit\n"]
