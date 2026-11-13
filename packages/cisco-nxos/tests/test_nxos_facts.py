import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from netsdk.cisco.nxos import NXOSFacts
from netsdk.cisco.nxos.parsers import parse_facts, rows
from netsdk.core import ParseError

NAPALM = Path(__file__).parent / "fixtures" / "napalm"

# What NX-OS 9.3 prints ahead of every `| json` output (Mahak's bench, 2026-11-13).
NOISE_93 = (
    "SB: Permission denied.\nSB: Permission denied.\nSB: Permission denied.\n"
    "drbg_instantiate failed\n"
)


def napalm(name: str) -> str:
    return (NAPALM / name).read_text()


def dumps(data: dict[str, Any]) -> str:
    # One long line, as 9.3 and 10.x print `| json`.
    return json.dumps(data)


def inventory(*records: dict[str, str]) -> str:
    row: Any = records[0] if len(records) == 1 else list(records)
    return dumps({"TABLE_inv": {"ROW_inv": row}})


CHASSIS = {
    "name": "Chassis",
    "desc": "Nexus9000 C93180YC-EX chassis",
    "productid": "N9K-C93180YC-EX",
    "vendorid": "V03",
    "serialnum": "FDO00000AAA",
}

# The 9.x/10.x key layout (hand-written; NAPALM has only a 7.3 sample).
VERSION_10 = {
    "bios_ver_str": "07.69",
    "nxos_ver_str": "10.4(5)",
    "nxos_file_name": "bootflash:///nxos64-cs.10.4.5.M.bin",
    "chassis_id": "Nexus9000 C93180YC-EX chassis",
    "proc_board_id": "FDO00000AAA",
    "host_name": "leaf1",
    "kern_uptm_days": 12,
    "kern_uptm_hrs": 3,
    "kern_uptm_mins": 4,
    "kern_uptm_secs": 5,
    "rr_reason": "Reset Requested by CLI command reload",
}


def test_napalm_73_sample() -> None:
    facts = parse_facts(
        napalm("nxapi_normal_show_version.json"), napalm("nxapi_normal_show_inventory.json")
    )
    assert facts == NXOSFacts(
        hostname="nxos-spine1",
        vendor="Cisco",
        model="N6K-C6001-64P",
        serial_number="TM6017D760B",
        os_version="7.3(1)D1(1) [build 7.3(1)D1(0.10)]",
        uptime=timedelta(days=47, hours=1, minutes=37, seconds=11),
        chassis_description="NX-OSv Chassis",
        running_image="bootflash:///titanium-d1.7.3.1.D1.0.10.bin",
        bios_version=None,
        reload_reason=None,
    )


def test_chassis_name_printed_with_its_quotes() -> None:
    facts = parse_facts(
        napalm("nxapi_normal_show_version.json"), napalm("ssh_N93180_show_inventory_json.txt")
    )
    assert facts.serial_number == "FDO21270U84"


def test_9x_10x_fields() -> None:
    facts = parse_facts(dumps(VERSION_10 | {"sys_ver_str": "ignored"}), inventory(CHASSIS))
    assert facts.os_version == "10.4(5)"
    assert facts.running_image == "bootflash:///nxos64-cs.10.4.5.M.bin"
    assert facts.bios_version == "07.69"
    assert facts.reload_reason == "Reset Requested by CLI command reload"
    assert facts.uptime == timedelta(days=12, hours=3, minutes=4, seconds=5)
    assert (facts.model, facts.serial_number) == ("N9K-C93180YC-EX", "FDO00000AAA")


def test_93_noise_before_the_json() -> None:
    facts = parse_facts(NOISE_93 + dumps(VERSION_10), NOISE_93 + inventory(CHASSIS))
    assert facts.hostname == "leaf1"


def test_fex_and_modules_are_not_the_chassis() -> None:
    fex = {"name": "FEX 101 CHASSIS", "productid": "N2K-C2348UPQ", "serialnum": "FOX0000BBB"}
    module = {"name": "Slot 1", "productid": "N9K-C93180YC-EX", "serialnum": "FDO0000CCC"}
    facts = parse_facts(dumps(VERSION_10), inventory(fex, CHASSIS, module))
    assert facts.serial_number == "FDO00000AAA"


@pytest.mark.parametrize(
    "records",
    [
        pytest.param((), id="no-chassis"),
        pytest.param((CHASSIS, CHASSIS), id="two-chassis"),
        pytest.param((CHASSIS | {"serialnum": "N/A"},), id="no-serial"),
        pytest.param((CHASSIS | {"productid": ""},), id="no-part-id"),
    ],
)
def test_chassis_problems_are_parse_errors(records: tuple[dict[str, str], ...]) -> None:
    show_inventory = inventory(*records) if records else dumps({"TABLE_inv": {}})
    with pytest.raises(ParseError):
        parse_facts(dumps(VERSION_10), show_inventory)


def test_uptime_as_strings_or_absent() -> None:
    as_strings = VERSION_10 | {"kern_uptm_days": "1", "kern_uptm_hrs": "0"}
    assert parse_facts(dumps(as_strings), inventory(CHASSIS)).uptime == timedelta(
        days=1, minutes=4, seconds=5
    )
    absent = {k: v for k, v in VERSION_10.items() if not k.startswith("kern_uptm")}
    assert parse_facts(dumps(absent), inventory(CHASSIS)).uptime is None


@pytest.mark.parametrize(
    "version",
    [
        pytest.param({k: v for k, v in VERSION_10.items() if k != "host_name"}, id="no-hostname"),
        pytest.param({k: v for k, v in VERSION_10.items() if k != "nxos_ver_str"}, id="no-version"),
        pytest.param(
            {k: v for k, v in VERSION_10.items() if k != "kern_uptm_secs"}, id="partial-uptime"
        ),
        pytest.param(VERSION_10 | {"kern_uptm_days": "many"}, id="uptime-not-a-number"),
        pytest.param(VERSION_10 | {"host_name": ["x"]}, id="hostname-not-a-string"),
    ],
)
def test_version_problems_are_parse_errors(version: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        parse_facts(dumps(version), inventory(CHASSIS))


def test_rejected_json_pipe_is_a_parse_error() -> None:
    # What an NX-OS without `| json` for the command prints (as output; the
    # transport turns it into a CommandError first).
    with pytest.raises(ParseError):
        parse_facts(
            napalm("nxapi_normal_show_version.json"),
            napalm("ssh_does_not_support_json_show_inventory_json.txt"),
        )


# -- rows() --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"TABLE_x": {"ROW_x": [{"a": 1}, {"a": 2}]}}, [{"a": 1}, {"a": 2}], id="list"),
        pytest.param({"TABLE_x": {"ROW_x": {"a": 1}}}, [{"a": 1}], id="single-object"),
        pytest.param({}, [], id="no-table"),
        pytest.param({"TABLE_x": {}}, [], id="no-rows"),
    ],
)
def test_rows(data: dict[str, Any], expected: list[dict[str, Any]]) -> None:
    assert rows(data, "TABLE_x", "ROW_x", "show x | json") == expected


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"TABLE_x": []}, id="table-not-object"),
        pytest.param({"TABLE_x": {"ROW_x": "text"}}, id="rows-not-objects"),
        pytest.param({"TABLE_x": {"ROW_x": [{"a": 1}, 2]}}, id="mixed-rows"),
    ],
)
def test_rows_rejects_other_shapes(data: dict[str, Any]) -> None:
    with pytest.raises(ParseError):
        rows(data, "TABLE_x", "ROW_x", "show x | json")
