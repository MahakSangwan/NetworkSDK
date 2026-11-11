import re
from pathlib import Path

import pytest
from ntc_templates.parse import parse_output

from netsdk.cisco.ios import CiscoIOS, IOSSSHTransport
from netsdk.cisco.ios.parsers import parse_show_inventory
from netsdk.core import Credentials, InventoryItem, ParseError, SupportsInventory
from netsdk.core.testing import FakeCLI, FakeConnection

FIXTURES = Path(__file__).parent / "fixtures" / "show_inventory"
GENIE = FIXTURES / "genieparser"
NTC = FIXTURES / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")


def test_c3850_sample() -> None:
    items = parse_show_inventory((GENIE / "iosxe_golden_output_c3850.txt").read_text())
    assert items[:3] == [
        InventoryItem(
            name="c38xx Stack",
            description="c38xx Stack",
            part_id="WS-C3850-24P-E",
            version_id="V01",
            serial_number="FCW1932D0LB",
        ),
        InventoryItem(
            name="Switch 1",
            description="WS-C3850-24P-E",
            part_id="WS-C3850-24P-E",
            version_id="V01",
            serial_number="FCW1932D0LB",
        ),
        InventoryItem(
            name="StackPort1/1",
            description="StackPort1/1",
            part_id="STACK-T1-50CM",
            version_id="V01",
            serial_number="LCC1921G11G",
        ),
    ]


def test_breakout_and_aoc_serials_keep_their_suffix() -> None:
    # The released template cuts these at "-" (D36): the four legs of one
    # breakout cable would all read "TED2639590G".
    items = {
        i.name: i
        for i in parse_show_inventory((GENIE / "iosxe_golden_output_c9500x.txt").read_text())
    }
    assert [items[f"FiftyGigE1/0/{n}"].serial_number for n in (1, 2, 3, 4)] == [
        "TED2639590G-CH1",
        "TED2639590G-CH2",
        "TED2639590G-CH3",
        "TED2639590G-CH4",
    ]
    assert items["FiftyGigE1/0/1"].part_id == "QDD-8S50-CU2.5M"
    assert items["FiftyGigE1/0/22"].serial_number == "INL2529A987-B"
    assert items["FiftyGigE1/0/29"].serial_number == "FIW251502LU-A"


def test_part_id_with_spaces() -> None:
    items = parse_show_inventory((GENIE / "iosxe_golden_output_1.txt").read_text())
    disk = next(i for i in items if i.name == "subslot 0/3 disk1")
    assert disk == InventoryItem(
        name="subslot 0/3 disk1",
        description="harddisk",
        part_id="Samsung SSD 860 EVO 250GB",
        version_id="RVT03B6Q",
        serial_number="S3Y9NX0M604745D",
    )


def test_blank_values_and_repeated_names() -> None:
    items = parse_show_inventory((NTC / "cisco_ios_show_inventory.raw").read_text())
    assert items[0] == InventoryItem(
        name="3640 chassis",
        description="3640 chassis",
        part_id=None,  # "PID:                   ,"
        version_id="0xFF",
        serial_number="FF1045C5",
    )
    # Identical modules listed under one name: every one is kept.
    assert [i.name for i in items[1:]] == ["One port Fastethernet TX"] * 3


def test_empty_name() -> None:
    (item,) = parse_show_inventory((GENIE / "ios_golden_output_12.txt").read_text())
    assert item == InventoryItem(
        name=None,  # 'NAME: ""'
        description="7206VXR chassis, Hw Serial#: 34942674, Hw Revision: B",
        part_id=None,
        version_id="2.11",
        serial_number="34942674",
    )


def _samples() -> list[Path]:
    return sorted([*GENIE.glob("*.txt"), *NTC.glob("*.raw")])


@pytest.mark.parametrize("sample", _samples(), ids=lambda p: p.name)
def test_every_sample(sample: Path) -> None:
    text = sample.read_text()
    items = parse_show_inventory(text)
    printed = re.findall(
        r"^[ \t]*PID:[ \t]*(.*?)[ \t]*,[ \t]*VID:[ \t]*(.*?)[ \t]*,[ \t]*SN:[ \t]*(\S*)",
        text,
        re.MULTILINE,
    )
    assert [(i.part_id or "", i.version_id or "", i.serial_number or "") for i in items] == printed


def test_released_template_still_truncates() -> None:
    # Why the corrected copy exists. When this fails, a released ntc-templates
    # has networktocode/ntc-templates#2377: delete the copy (D36).
    text = (GENIE / "iosxe_golden_output_c9500x.txt").read_text()
    rows = parse_output(platform="cisco_ios", command="show inventory", data=text)
    assert "TED2639590G" in {r["sn"] for r in rows}


def test_unparseable_output_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_show_inventory("junk\nlines\n")


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_inventory_sends_only_show_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = (GENIE / "iosxe_golden_output_c3850.txt").read_text().replace("\n", "\r\n")
    conn = FakeCLI({"show inventory": [text.rstrip("\r\n") + "\r\n"]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsInventory)
        items = device.get_inventory()
    assert len(items) == 21
    assert conn.writes == ["show inventory\n", "exit\n"]
