import logging
from pathlib import Path

import pytest

from netsdk.cisco.ios import CiscoIOS, IOSInterface, IOSSSHTransport
from netsdk.cisco.ios.parsers import _duplex, _speed, parse_show_interfaces
from netsdk.core import Credentials, ParseError, SupportsInterfaces
from netsdk.core.testing import FakeCLI, FakeConnection

UPSTREAM = Path(__file__).parent / "fixtures" / "show_interfaces" / "ntc_templates"

CREDS = Credentials(username="ro", password="pw")

# Hand-written: an access port that is up, one that is shut, and an SVI.
SHOW_INTERFACES = (
    "GigabitEthernet1/0/1 is up, line protocol is up (connected)\r\n"
    "  Hardware is Gigabit Ethernet, address is 0014.1c57.a481 (bia 0014.1c57.a481)\r\n"
    "  Description: uplink to core-1\r\n"
    "  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec,\r\n"
    "     reliability 255/255, txload 1/255, rxload 1/255\r\n"
    "  Encapsulation ARPA, loopback not set\r\n"
    "  Full-duplex, 1000Mb/s, media type is 10/100/1000BaseTX\r\n"
    "GigabitEthernet1/0/2 is administratively down, line protocol is down (disabled)\r\n"
    "  Hardware is Gigabit Ethernet, address is 0014.1c57.a482 (bia 0014.1c57.a482)\r\n"
    "  MTU 1500 bytes, BW 10000 Kbit/sec, DLY 1000 usec,\r\n"
    "  Auto-duplex, Auto-speed, media type is 10/100/1000BaseTX\r\n"
    "Vlan10 is up, line protocol is up\r\n"
    "  Hardware is EtherSVI, address is 0014.1c57.a4c0 (bia 0014.1c57.a4c0)\r\n"
    "  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec,\r\n"
)


def read(name: str) -> str:
    return (UPSTREAM / f"{name}.raw").read_text()


def by_name(interfaces: list[IOSInterface]) -> dict[str, IOSInterface]:
    return {interface.name: interface for interface in interfaces}


def test_hand_written_sample() -> None:
    up, shut, svi = parse_show_interfaces(SHOW_INTERFACES)
    assert up == IOSInterface(
        name="GigabitEthernet1/0/1",
        description="uplink to core-1",
        enabled=True,
        oper_up=True,
        mac_address="00:14:1c:57:a4:81",
        mtu=1500,
        speed=1_000_000,
        duplex="full",
        link_status="up",
        protocol_status="up (connected)",
        hardware_type="Gigabit Ethernet",
        media_type="10/100/1000BaseTX",
        burned_in_address="00:14:1c:57:a4:81",
        bandwidth=1_000_000,
    )
    assert (shut.enabled, shut.oper_up, shut.speed, shut.duplex) == (False, False, None, "auto")
    assert shut.protocol_status == "down (disabled)"
    assert (svi.name, svi.speed, svi.duplex, svi.description) == ("Vlan10", None, None, None)


@pytest.mark.parametrize(
    ("name", "count"),
    [
        ("cisco_ios_show_interfaces2", 56),
        ("cisco_ios_show_interfaces3", 14),
        ("cisco_ios_show_interfaces4", 16),
        ("cisco_ios_show_interfaces5", 2),
        ("cisco_ios_show_interfaces6", 4),
        ("cisco_ios_show_interfaces_01", 3),
    ],
)
def test_upstream_samples_parse_without_warnings(
    name: str, count: int, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        interfaces = parse_show_interfaces(read(name))
    assert len(interfaces) == count
    assert caplog.records == [], "a speed or duplex spelling was not recognised"


def test_all_upstream_samples_are_covered() -> None:
    assert len(list(UPSTREAM.glob("*.raw"))) == 7


def test_upstream_values_match_the_raw_text() -> None:
    # Spot checks read off the .raw files by hand.
    half = by_name(parse_show_interfaces(read("cisco_ios_show_interfaces4")))["FastEthernet1"]
    assert (half.enabled, half.oper_up, half.speed, half.duplex) == (True, False, 10_000, "half")
    assert half.mac_address == "6c:41:6a:ba:b4:7f"
    assert half.hardware_type == "RP management port"

    tunnel = by_name(parse_show_interfaces(read("cisco_ios_show_interfaces3")))["Tunnel3"]
    assert (tunnel.mtu, tunnel.bandwidth, tunnel.mac_address, tunnel.speed) == (
        17912,
        100,
        None,
        None,
    )

    # Shut down, but still reports its configured 10Gb/s.
    shut = by_name(parse_show_interfaces(read("cisco_ios_show_interfaces6")))[
        "TwentyFiveGigE2/0/32"
    ]
    assert (shut.enabled, shut.speed, shut.duplex, shut.bandwidth) == (
        False,
        10_000_000,
        "full",
        25_000_000,
    )


def test_spliced_capture_with_duplicate_interface_is_rejected() -> None:
    with pytest.raises(ParseError, match="GigabitEthernet0/2 more than once"):
        parse_show_interfaces(read("cisco_ios_show_interfaces"))


@pytest.mark.parametrize(
    ("text", "kbps"),
    [
        ("10Mb/s", 10_000),
        ("100Mbps", 100_000),
        ("1000Mb/s", 1_000_000),
        ("a-1000Mb/s", 1_000_000),
        ("10Gb/s", 10_000_000),
        ("10000Mbps", 10_000_000),
        ("100Gb/s", 100_000_000),
        ("Auto-speed", None),
        ("Auto Speed", None),
        ("", None),
    ],
)
def test_speed_spellings(text: str, kbps: int | None) -> None:
    assert _speed("Gi1", text) == kbps


@pytest.mark.parametrize(
    ("text", "duplex"),
    [
        ("Full-duplex", "full"),
        ("Full Duplex", "full"),
        ("Half-duplex", "half"),
        ("Auto-duplex", "auto"),
        ("Auto Duplex", "auto"),
        ("a-full", "full"),
        ("", None),
    ],
)
def test_duplex_spellings(text: str, duplex: str | None) -> None:
    assert _duplex("Gi1", text) == duplex


def test_unknown_spellings_are_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    assert _speed("Gi1", "fast") is None
    assert _duplex("Gi1", "Sideways-duplex") is None
    assert len(caplog.records) == 2


# -- device ----------------------------------------------------------------


class FastIOSTransport(IOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoIOS:
    monkeypatch.setattr(FastIOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoIOS, "transport_class", FastIOSTransport)
    return CiscoIOS("sw1.example", CREDS, timeout=1.0, session_log_dir=tmp_path)


def test_get_interfaces_sends_only_show_interfaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = FakeCLI({"show interfaces": [SHOW_INTERFACES]}, hostname="sw1").connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsInterfaces)
        interfaces = device.get_interfaces()
        raw = device.get_interfaces_raw()
    assert [i.name for i in interfaces] == [
        "GigabitEthernet1/0/1",
        "GigabitEthernet1/0/2",
        "Vlan10",
    ]
    assert list(raw) == ["show interfaces"]
    assert conn.writes == ["show interfaces\n", "show interfaces\n", "exit\n"]
