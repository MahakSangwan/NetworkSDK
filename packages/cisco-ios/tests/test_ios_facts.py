from datetime import timedelta
from pathlib import Path

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSFacts, IOSSSHTransport
from netsdk.cisco.ios.parsers import parse_show_version
from netsdk.core import Credentials, NotSupportedError, ParseError, SupportsFacts
from netsdk.core.testing import FakeCLI, FakeConnection

UPSTREAM = Path(__file__).parent / "fixtures" / "show_version" / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# A standalone switch with every optional line present (hand-written).
SHOW_VERSION_2960X = (
    "Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E4,"
    " RELEASE SOFTWARE (fc2)\r\n"
    "ROM: Bootstrap program is C2960X boot loader\r\n"
    "sw1 uptime is 1 year, 3 weeks, 2 days, 4 hours, 12 minutes\r\n"
    "System returned to ROM by power-on\r\n"
    'System image file is "flash:/c2960x-universalk9-mz.152-7.E4.bin"\r\n'
    "cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision B0) with 524288K bytes of memory.\r\n"
    "Processor board ID FOC1234X0AB\r\n"
    "Model number                    : WS-C2960X-48FPD-L\r\n"
    "System serial number            : FOC1234X0AB\r\n"
    "Configuration register is 0xF\r\n"
)


def read(name: str) -> str:
    return (UPSTREAM / f"{name}.raw").read_text()


@pytest.mark.parametrize(
    ("name", "hostname", "model", "serial", "version", "uptime"),
    [
        (
            "cisco_ios_show_version",
            "router1",
            "WS-C4948E",
            "CAT1451S15C",
            "12.2(54)SG1",
            timedelta(days=2 * 365 + 31 * 7 + 6, hours=9, minutes=55),
        ),
        (
            "cisco_ios_show_version_01",
            "stud-adsl",
            "1801",
            "FHK102KKKKK",
            "12.4(6)T2",
            timedelta(weeks=20, days=5, hours=12, minutes=15),
        ),
        (
            "cisco_ios_show_version6",
            "Switch5",
            "IOSv",
            "9RYX5605M6M",
            "15.2(4.0.55)E",
            timedelta(weeks=3, days=6, minutes=32),
        ),
        (
            "cisco_ios_show_version2",
            "my_device",
            "ASR1001",
            "SSI1289001MH",
            "15.3(2)S1",
            timedelta(days=1, hours=15, minutes=32),
        ),
        (
            "cisco_ios_show_version3",
            "rtr-01",
            "IOSv",
            "991UCMIHG4UAJ1J010CQG",
            "15.8(3)M2",
            timedelta(weeks=1, days=3, hours=16, minutes=11),
        ),
    ],
)
def test_upstream_single_chassis_samples(
    name: str, hostname: str, model: str, serial: str, version: str, uptime: timedelta
) -> None:
    facts = parse_show_version(read(name))
    assert (facts.hostname, facts.model, facts.serial_number, facts.os_version) == (
        hostname,
        model,
        serial,
        version,
    )
    assert facts.vendor == "Cisco"
    assert facts.uptime == uptime


@pytest.mark.parametrize(
    "name",
    [
        "cisco_ios_show_version1",  # 3850 stack of 4
        "cisco_ios_show_version4",  # 9500 StackWise Virtual pair
        "cisco_ios_show_version5",
        "cisco_ios_show_version_special_build",
    ],
)
def test_upstream_stacks_are_rejected(name: str) -> None:
    with pytest.raises(NotSupportedError, match="stacks are not supported"):
        parse_show_version(read(name))


def test_all_upstream_samples_are_covered() -> None:
    # A newly copied sample must be added to one of the tests above.
    assert len(list(UPSTREAM.glob("*.raw"))) == 9


def test_optional_fields() -> None:
    facts = parse_show_version(SHOW_VERSION_2960X)
    assert facts == IOSFacts(
        hostname="sw1",
        vendor="Cisco",
        model="WS-C2960X-48FPD-L",
        serial_number="FOC1234X0AB",
        os_version="15.2(7)E4",
        uptime=timedelta(days=365 + 3 * 7 + 2, hours=4, minutes=12),
        software_image="C2960X-UNIVERSALK9-M",
        running_image="/c2960x-universalk9-mz.152-7.E4.bin",
        config_register="0xF",
        reload_reason="power-on",
    )


def test_missing_optional_lines_become_none() -> None:
    minimal = "\r\n".join(
        line
        for line in SHOW_VERSION_2960X.split("\r\n")
        if not line.startswith(("System returned", "System image", "Configuration register"))
    )
    facts = parse_show_version(minimal)
    assert (facts.running_image, facts.config_register, facts.reload_reason) == (None,) * 3


def test_missing_serial_number_is_a_parse_error() -> None:
    no_serial = "\r\n".join(
        line
        for line in SHOW_VERSION_2960X.split("\r\n")
        if "serial number" not in line and "board ID" not in line
    )
    with pytest.raises(ParseError, match="no serial number"):
        parse_show_version(no_serial)


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_facts_sends_only_show_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = FakeCLI({"show version": [SHOW_VERSION_2960X]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsFacts)
        facts = device.get_facts()
    assert facts.serial_number == "FOC1234X0AB"
    assert conn.writes == ["show version\n", "exit\n"]


def test_get_facts_raw_is_the_command_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show version": [SHOW_VERSION_2960X]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        raw = device.get_facts_raw()
    assert list(raw) == ["show version"]
    assert raw["show version"].startswith("Cisco IOS Software, C2960X Software")
    assert "sw1>" not in raw["show version"]


def test_parse_errors_name_the_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = FakeCLI({"show version": ["nothing useful\r\n"]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device, pytest.raises(ParseError) as excinfo:
        device.get_facts()
    assert excinfo.value.host == "sw1.example"
    assert str(excinfo.value).startswith("[sw1.example]")
