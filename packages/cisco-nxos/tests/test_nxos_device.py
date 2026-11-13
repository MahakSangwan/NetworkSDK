import json
from pathlib import Path

import pytest

from netsdk.cisco.nxos import CiscoNXOS, NXOSSSHTransport
from netsdk.cisco.nxos.commands import SHOW_INVENTORY, SHOW_VERSION
from netsdk.core import CommandError, Credentials, ParseError, SupportsFacts
from netsdk.core.testing import FakeCLI, FakeConnection

CREDS = Credentials(username="ro", password="pw")
NAPALM = Path(__file__).parent / "fixtures" / "napalm"

NOISE_93 = (
    "SB: Permission denied.\r\nSB: Permission denied.\r\nSB: Permission denied.\r\n"
    "drbg_instantiate failed\r\n"
)


def crlf(text: str) -> str:
    return text.rstrip("\n").replace("\n", "\r\n") + "\r\n"


VERSION_JSON = crlf((NAPALM / "nxapi_normal_show_version.json").read_text())
INVENTORY_JSON = crlf((NAPALM / "nxapi_normal_show_inventory.json").read_text())


class FastNXOSTransport(NXOSSSHTransport):
    prompt_wait = 0.2
    exit_wait = 0.2
    poll_interval = 0.001


def make_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conn: FakeConnection) -> CiscoNXOS:
    monkeypatch.setattr(FastNXOSTransport, "_connect", lambda self, log_file: conn)
    monkeypatch.setattr(CiscoNXOS, "transport_class", FastNXOSTransport)
    return CiscoNXOS(
        "sw1.example",
        CREDS,
        timeout=1.0,
        session_log_dir=tmp_path / "logs",
        known_hosts_file=tmp_path / "known_hosts",
    )


def nxos_cli(outputs: dict[str, list[str]], **kwargs: str) -> FakeCLI:
    return FakeCLI(outputs, hostname="sw1", terminator="# ", **kwargs)


def test_get_facts_sends_only_its_two_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = nxos_cli({SHOW_VERSION: [VERSION_JSON], SHOW_INVENTORY: [INVENTORY_JSON]})
    conn = cli.connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        assert isinstance(device, SupportsFacts)
        facts = device.get_facts()
    assert (facts.hostname, facts.model) == ("nxos-spine1", "N6K-C6001-64P")
    assert conn.writes == [f"{SHOW_VERSION}\n", f"{SHOW_INVENTORY}\n", "exit\n"]


def test_93_noise_and_one_line_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    one_line = {
        cmd: NOISE_93 + json.dumps(json.loads(text)) + "\r\n"
        for cmd, text in ((SHOW_VERSION, VERSION_JSON), (SHOW_INVENTORY, INVENTORY_JSON))
    }
    cli = nxos_cli({cmd: [text] for cmd, text in one_line.items()})
    with make_device(monkeypatch, tmp_path, cli.connection()) as device:
        assert device.get_facts().serial_number == "TM6017D760B"


@pytest.mark.parametrize(
    ("pager", "erase"),
    [
        pytest.param("--More--", "\r        \r", id="plain"),
        pytest.param("\x1b[7m--More--\x1b[m", "\r\x1b[K", id="reverse-video"),
    ],
)
def test_paged_json_is_joined_back_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pager: str, erase: str
) -> None:
    # Multi-line JSON, as 7.3(11)N1(1) prints it, split over three pages.
    lines = INVENTORY_JSON.splitlines(keepends=True)
    pages = ["".join(lines[:12]), "".join(lines[12:24]), "".join(lines[24:])]
    cli = nxos_cli({SHOW_VERSION: [VERSION_JSON], SHOW_INVENTORY: pages}, pager=pager, erase=erase)
    conn = cli.connection()
    with make_device(monkeypatch, tmp_path, conn) as device:
        raw = device.get_facts_raw()
        facts = device.get_facts()
    assert "More" not in raw[SHOW_INVENTORY]
    assert "\x1b" not in raw[SHOW_INVENTORY]
    assert facts.serial_number == "TM6017D760B"
    # The pager key is the only extra write.
    assert conn.writes.count(" ") == 4
    assert [w for w in conn.writes if w != " "] == [
        f"{SHOW_VERSION}\n",
        f"{SHOW_INVENTORY}\n",
        f"{SHOW_VERSION}\n",
        f"{SHOW_INVENTORY}\n",
        "exit\n",
    ]


def test_command_without_json_support_is_a_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rejected = crlf((NAPALM / "ssh_does_not_support_json_show_inventory_json.txt").read_text())
    cli = nxos_cli({SHOW_VERSION: [VERSION_JSON], SHOW_INVENTORY: [rejected]})
    with (
        make_device(monkeypatch, tmp_path, cli.connection()) as device,
        pytest.raises(CommandError) as excinfo,
    ):
        device.get_facts()
    assert "% Invalid command" in str(excinfo.value)
    assert excinfo.value.host == "sw1.example"


def test_rejection_after_the_93_noise_is_still_seen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rejected = NOISE_93 + "                   ^\r\n% Permission denied for the role\r\n"
    cli = nxos_cli({SHOW_VERSION: [rejected]})
    with (
        make_device(monkeypatch, tmp_path, cli.connection()) as device,
        pytest.raises(CommandError),
    ):
        device.get_facts()


def test_unparseable_output_names_the_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cli = nxos_cli({SHOW_VERSION: [VERSION_JSON], SHOW_INVENTORY: ['{"TABLE_inv": \r\n']})
    with (
        make_device(monkeypatch, tmp_path, cli.connection()) as device,
        pytest.raises(ParseError) as excinfo,
    ):
        device.get_facts()
    assert excinfo.value.host == "sw1.example"
