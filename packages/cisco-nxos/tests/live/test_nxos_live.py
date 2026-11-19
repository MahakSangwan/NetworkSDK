"""Live tests against a real NX-OS switch. Run manually: `pytest -m live -s`.

Configuration comes from environment variables, never from code. Set a
password, a key, or both; tests log in with whatever is configured.

    NETSDK_NXOS_HOST          switch to test (required; standalone NX-OS, not ACI)
    NETSDK_NXOS_PORT          SSH port (default 22)
    NETSDK_USERNAME           read-only account (required)
    NETSDK_PASSWORD           password
    NETSDK_KEY_FILE           private key path (OpenSSH format, not PuTTY .ppk)
    NETSDK_KEY_PASSPHRASE     key passphrase, if the key has one
    NETSDK_NXOS_LONG_COMMAND  command long enough to page (default "show interface")
    NETSDK_UNREACHABLE_HOST   host that drops SSH (default 192.0.2.1, TEST-NET-1)
    NETSDK_SESSION_LOG_DIR    where session logs go (default "session_logs")
    NETSDK_LIVE_AUTH_FAILURE  set to 1 to run the deliberate failed-login test
                              (off by default: it can count towards AAA lockout)

Everything but the failure tests runs in one shared session. Printed output
has no hostnames, serials or addresses: it gets pasted back for review.
"""

import json
import logging
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from netsdk.cisco.nxos import CiscoNXOS
from netsdk.cisco.nxos.commands import (
    SHOW_CDP_NEIGHBORS_DETAIL,
    SHOW_INTERFACE,
    SHOW_INTERFACE_SWITCHPORT,
    SHOW_INTERFACE_TRUNK,
    SHOW_INVENTORY,
    SHOW_IP_INTERFACE,
    SHOW_LLDP_NEIGHBORS_DETAIL,
    SHOW_VERSION,
    SHOW_VLAN_BRIEF,
    SHOW_VTP_STATUS,
)
from netsdk.cisco.nxos.parsers import rows, text, vlan_ids
from netsdk.core import (
    AuthenticationError,
    CommandError,
    Credentials,
    DeviceConnectionError,
    IPAddress,
    NotSupportedError,
)
from netsdk.core.parsing import Damaged, parse_json

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_NXOS_HOST")
PORT = int(_env("NETSDK_NXOS_PORT") or 22)
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")
LONG_COMMAND = _env("NETSDK_NXOS_LONG_COMMAND") or "show interface"
UNREACHABLE_HOST = _env("NETSDK_UNREACHABLE_HOST") or "192.0.2.1"
LOG_DIR = Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs")
AUTH_FAILURE_TEST = _env("NETSDK_LIVE_AUTH_FAILURE") == "1"

#: A 24-line terminal so the switch pages often and every test meets the pager.
PAGED_HEIGHT = 24

needs_device = pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_NXOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)


def configured_creds() -> Credentials:
    assert USERNAME
    return Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        key_file=Path(KEY_FILE) if KEY_FILE else None,
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )


def device(
    creds: Credentials, host: str | None = None, timeout: float = 30.0, **kwargs: Any
) -> CiscoNXOS:
    target = host or HOST
    assert target
    return CiscoNXOS(target, creds, port=PORT, timeout=timeout, session_log_dir=LOG_DIR, **kwargs)


@pytest.fixture(scope="module")
def shared() -> Iterator[CiscoNXOS]:
    with device(configured_creds(), timeout=120.0, terminal_height=PAGED_HEIGHT) as dev:
        yield dev


# -- session behaviour -----------------------------------------------------------


@needs_device
def test_login(shared: CiscoNXOS) -> None:
    methods = [name for name, value in (("password", PASSWORD), ("key", KEY_FILE)) if value]
    print(f"\nlogged in with: {' or '.join(methods)}")
    assert shared.send_command("show clock")


@needs_device
def test_show_terminal(shared: CiscoNXOS) -> None:
    # Reported, not asserted: whether NX-OS takes its length from our PTY.
    output = shared.send_command("show terminal")
    sizes = [line for line in output.splitlines() if "Length" in line or "Width" in line]
    print("\n" + ("\n".join(sizes) or "no Length/Width lines; full output:\n" + output))


@needs_device
def test_long_output_is_paged_cleanly(shared: CiscoNXOS) -> None:
    output = shared.send_command(LONG_COMMAND)
    lines = output.splitlines()
    print(f"\n{LONG_COMMAND!r}: {len(lines)} lines on a {PAGED_HEIGHT}-line terminal")
    assert len(lines) > PAGED_HEIGHT, "output fits one page; set NETSDK_NXOS_LONG_COMMAND"
    for bad in ("--More--", "\b", "\r", "\x1b"):
        assert bad not in output, f"{bad!r} left in the output"


@needs_device
def test_rejected_command_raises_and_session_survives(shared: CiscoNXOS) -> None:
    with pytest.raises(CommandError) as excinfo:
        shared.send_command("show netsdk-no-such-command")
    print(f"\nrejected as: {excinfo.value}")
    assert shared.send_command("show clock")


# -- getters -------------------------------------------------------------------


@needs_device
def test_json_layout(shared: CiscoNXOS, caplog: pytest.LogCaptureFixture) -> None:
    # Key names only (no values): confirms the fields get_facts() reads.
    with caplog.at_level(logging.INFO, logger="netsdk"):
        raw = shared.get_facts_raw()
        version = _json(shared, SHOW_VERSION, raw[SHOW_VERSION])
        inventory = _json(shared, SHOW_INVENTORY, raw[SHOW_INVENTORY])
    for command, output in raw.items():
        widths = sorted({len(line) for line in output.splitlines()}, reverse=True)[:3]
        print(f"\n{command!r}: {len(output.splitlines())} line(s), longest {widths}")
    print(f"show version keys: {', '.join(sorted(version))}")
    print(f"show inventory keys: {', '.join(sorted(inventory))}")
    records = rows(inventory, "TABLE_inv", "ROW_inv", SHOW_INVENTORY)
    print(f"{len(records)} inventory entries")
    print(f"{'name':<34} {'part id':<24} {'sn':<3} description")
    for r in records:
        print(
            f"{json.dumps(r.get('name')):<34} {text(r, 'productid') or '-':<24} "
            f"{'set' if text(r, 'serialnum') else '-':<3} {text(r, 'desc') or '-'}"
        )
    for record in caplog.records:
        print(f"{record.levelname}: {record.getMessage()}")
    assert records


@needs_device
def test_get_facts(shared: CiscoNXOS) -> None:
    facts = shared.get_facts()
    shown = facts.model_dump(exclude={"hostname", "serial_number"})
    print("\nhostname: set\nserial_number: set")
    print("\n".join(f"{name}: {value}" for name, value in shown.items()))
    # Reported, not asserted: whether the board ID in `show version` is the
    # chassis serial (expected on fixed switches, not on modular ones).
    version = _json(shared, SHOW_VERSION, shared.get_facts_raw()[SHOW_VERSION])
    board = text(version, "proc_board_id")
    print(f"show version board ID is the chassis serial: {board == facts.serial_number}")
    assert facts.vendor == "Cisco"


#: Field names get_interfaces() reads, per field set.
_ETH_FIELDS = [
    "admin_state",
    "state",
    "state_rsn_desc",
    "desc",
    "eth_hw_addr",
    "eth_mtu",
    "eth_speed",
    "eth_duplex",
    "eth_hw_desc",
    "eth_bia_addr",
    "eth_bw",
    "eth_mode",
]
_SVI_FIELDS = [
    "svi_admin_state",
    "svi_line_proto",
    "svi_rsn_desc",
    "svi_desc",
    "svi_mac",
    "svi_mtu",
    "svi_bw",
]
_DESCRIBE = re.compile(r"state|desc|mtu|speed|duplex|addr|mac|bw|mode|proto")


@needs_device
def test_interface_field_sets(shared: CiscoNXOS) -> None:
    # Field names only (no values), per interface type: confirms the mapping.
    data = _json(shared, SHOW_INTERFACE, shared.get_interfaces_raw()[SHOW_INTERFACE])
    records = rows(data, "TABLE_interface", "ROW_interface", SHOW_INTERFACE)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_type.setdefault(re.sub(r"[\d/.]+$", "", str(r.get("interface"))), []).append(r)
    for kind, group in sorted(by_type.items()):
        keys = set().union(*group)
        field_set = "svi" if "svi_admin_state" in keys else "eth" if "state" in keys else "?"
        used = _SVI_FIELDS if field_set == "svi" else _ETH_FIELDS
        never = [k for k in used if k not in keys]
        print(f"\n{kind}: {len(group)} interface(s), field set {field_set}")
        print(f"  never present: {', '.join(never) or '-'}")
        other = sorted(k for k in keys - set(used) if _DESCRIBE.search(k))
        print(f"  other state/address-like fields: {', '.join(other) or '-'}")
    assert records


@needs_device
def test_get_interfaces(shared: CiscoNXOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        interfaces = shared.get_interfaces()
    print(f"\n{len(interfaces)} interfaces")
    # No MACs or descriptions: this output gets pasted back for review.
    print(f"{'name':<22} {'en':<3} {'up':<3} {'speed':>10} {'duplex':<6} {'mtu':>5} mode/reason")
    for i in interfaces:
        print(
            f"{i.name:<22} {i.enabled!s:<3.1} {i.oper_up!s:<3.1} {i.speed or '-':>10} "
            f"{i.duplex or '-':<6} {i.mtu or '-':>5} {i.port_mode or '-'}/{i.state_reason or '-'}"
        )
    with_mac = sum(i.mac_address is not None for i in interfaces)
    with_desc = sum(i.description is not None for i in interfaces)
    print(f"{with_mac} of {len(interfaces)} have a MAC address, {with_desc} a description")
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    assert interfaces


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return f"list of {len(value)}"
    return type(value).__name__


@needs_device
def test_ip_interface_layout(shared: CiscoNXOS) -> None:
    # Structure and field names only (no values): confirms how VRFs pair up.
    data = _json(shared, SHOW_IP_INTERFACE, shared.get_ip_addresses_raw()[SHOW_IP_INTERFACE])
    print(f"\ntop-level keys: {', '.join(data)}")
    for table in ("TABLE_vrf", "TABLE_intf"):
        entries = data.get(table)
        print(f"{table}: {_shape(entries)}")
        items = entries if isinstance(entries, list) else [entries] if entries else []
        print(f"  entry keys: {sorted({k for e in items if isinstance(e, dict) for k in e})}")
        row_values = [v for e in items if isinstance(e, dict) for v in e.values()]
        print(f"  row value per entry: {[_shape(v) for v in row_values]}")
        rows_ = [
            r
            for v in row_values
            for r in (v if isinstance(v, list) else [v])
            if isinstance(r, dict)
        ]
        print(f"  row keys: {sorted({k for r in rows_ for k in r})}")
        nested = {k: _shape(r[k]) for r in rows_ for k in r if isinstance(r[k], dict | list)}
        print(f"  nested in rows: {nested or '-'}")
        secondary = [r["TABLE_secondary_address"] for r in rows_ if "TABLE_secondary_address" in r]
        if secondary:
            keys = {
                k
                for t in secondary
                for row in ([t.get("ROW_secondary_address")] if isinstance(t, dict) else [])
                for item in (row if isinstance(row, list) else [row])
                if isinstance(item, dict)
                for k in item
            }
            print(f"  secondary address keys: {sorted(keys)}")


_VRF_HEADING = re.compile(r'^IP Interface Status for VRF "([^"]+)"')
_BRIEF_ROW = re.compile(r"^\S+\s+(\d+\.\d+\.\d+\.\d+)\s")


@needs_device
def test_get_ip_addresses(shared: CiscoNXOS) -> None:
    addresses = shared.get_ip_addresses()
    # No addresses or VRF names: this output gets pasted back for review.
    named = sorted({a.vrf for a in addresses if a.vrf and a.vrf != "management"})

    def label(vrf: str | None) -> str:
        if vrf is None:
            return "global"
        return "management" if vrf == "management" else f"VRF {named.index(vrf) + 1}"

    print(f"\n{len(addresses)} addresses in {len(named)} other VRF(s) + management + global")
    print(f"{'interface':<22} {'prefixes':<16} {'secondary':<9} vrf")
    by_interface: dict[str, list[IPAddress]] = {}
    for address in addresses:
        by_interface.setdefault(address.interface, []).append(address)
    for name, records in by_interface.items():
        prefixes = ",".join(f"/{a.address.network.prefixlen}" for a in records)
        secondary = "yes" if any(a.secondary for a in records) else "-"
        print(f"{name:<22} {prefixes:<16} {secondary:<9} {label(records[0].vrf)}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted(set(by_interface) - interface_names)
    assert not unknown, f"addresses on interfaces get_interfaces() doesn't list: {unknown}"

    # Cross-check the VRF pairing against the text output, which prints a
    # heading per VRF: every primary (VRF, address) must match.
    brief: Counter[tuple[str | None, str]] = Counter()
    vrf: str | None = None
    for line in shared.send_command("show ip interface brief vrf all").splitlines():
        if heading := _VRF_HEADING.match(line):
            vrf = None if heading[1] == "default" else heading[1]
        elif row := _BRIEF_ROW.match(line):
            brief[(vrf, row[1])] += 1
    ours = Counter((a.vrf, str(a.address.ip)) for a in addresses if not a.secondary)
    only_ours, only_brief = ours - brief, brief - ours
    print(f"primary addresses: {sum(ours.values())} here, {sum(brief.values())} in the brief text")
    differ = (sum(only_ours.values()), sum(only_brief.values()))
    print(f"VRF/address pairs that differ: {differ[0]} here, {differ[1]} there")
    known = {None, "management", *named}
    for side, diff in (("here only", only_ours), ("brief only", only_brief)):
        for (v, _), count in diff.items():
            where = label(v) if v in known else "a VRF with no addresses here"
            print(f"  {side}: {count} in {where}")
    assert not only_ours, "get_ip_addresses() has VRF/address pairs the brief text lacks"
    assert not only_brief, "the brief text has VRF/address pairs get_ip_addresses() lacks"


# -- layout probes (slice 4) ---------------------------------------------------

#: Fields whose values are printed: modes and states, never names or numbers.
_ENUM_KEY = re.compile(
    r"mode|state|status|switchport|encap|negotiat|monitor|proto|version|pruning|trap|v2|capab|duplex",
    re.I,
)
_NOT_ENUM_KEY = re.compile(r"name|desc|platform|addr|id$|ver(sion)?_?str", re.I)
_KINDS = [
    ("empty", re.compile(r"")),
    ("number", re.compile(r"\d+")),
    ("vlan list", re.compile(r"\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)+|\d+-\d+")),
    ("interface name", re.compile(r"[A-Za-z][A-Za-z-]*\d[\d/.:]*")),
    (
        "interface list",
        re.compile(r"[A-Za-z][A-Za-z-]*\d[\d/.:-]*(?:\s*,\s*[A-Za-z][A-Za-z-]*\d[\d/.:-]*)+"),
    ),
    ("word", re.compile(r"[A-Za-z][A-Za-z ()-]{0,30}")),
]


def _kind(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int | float):
        return "int"
    if not isinstance(value, str):
        return type(value).__name__
    for kind, pattern in _KINDS:
        if pattern.fullmatch(value.strip()):
            if kind.startswith("interface") and re.search(r"\d-\d", value):
                return kind + " with ranges"
            return kind
    return "text"


def _walk(
    value: Any, path: str, kinds: dict[str, Counter[str]], enums: dict[str, set[str]]
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else key, kinds, enums)
    elif isinstance(value, list):
        kinds[path][f"list of {len(value)}"] += 1
        for item in value:
            _walk(item, path + "[]", kinds, enums)
    else:
        kind = _kind(value)
        kinds[path][kind] += 1
        key = path.rsplit(".", 1)[-1]
        if kind == "word" and _ENUM_KEY.search(key) and not _NOT_ENUM_KEY.search(key):
            enums.setdefault(path, set()).add(value)


@needs_device
@pytest.mark.parametrize(
    "command",
    [
        SHOW_VLAN_BRIEF,
        SHOW_INTERFACE_SWITCHPORT,
        SHOW_INTERFACE_TRUNK,
        SHOW_VTP_STATUS,
        SHOW_CDP_NEIGHBORS_DETAIL,
        SHOW_LLDP_NEIGHBORS_DETAIL,
    ],
)
def test_layout_probe(shared: CiscoNXOS, command: str) -> None:
    # Field paths and value kinds only; values only for mode/state fields.
    try:
        output = shared.send_command(command)
    except CommandError as exc:
        # Rejected: is it the `| json` or the command itself? Only whether the
        # plain command is accepted is printed, not its output.
        plain = command.removesuffix(" | json")
        try:
            shared.send_command(plain)
            verdict = f"{plain!r} is accepted: this release has no `| json` for it"
        except CommandError:
            verdict = f"{plain!r} is rejected too: the feature is off (or the command unknown)"
        pytest.skip(f"{command!r} rejected ({exc}); {verdict}")
    data = _json(shared, command, output)
    kinds: defaultdict[str, Counter[str]] = defaultdict(Counter)
    enums: dict[str, set[str]] = {}
    _walk(data, "", kinds, enums)
    print(f"\n{command!r}: {len(output.splitlines())} line(s)")
    for path, counter in sorted(kinds.items()):
        summary = ", ".join(f"{kind} x{n}" for kind, n in counter.most_common())
        print(f"  {path}: {summary}")
        if path in enums:
            print(f"    values: {sorted(enums[path])}")


def _json(device: CiscoNXOS, command: str, output: str) -> dict[str, Any]:
    return parse_json(command, output, wrap_width=device.terminal_width)


def _listed_ports(value: Any) -> set[str] | None:
    """A `show vlan brief` port list, ranges expanded; None when damaged."""
    parts: list[Any] = [value] if isinstance(value, str | Damaged) else list(value or [])
    if not all(isinstance(part, str) for part in parts):
        return None
    ports: set[str] = set()
    for item in ",".join(parts).split(","):
        match = re.fullmatch(r"(.*?)(\d+)-(\d+)", item.strip())
        if match is None:
            ports.add(item.strip())
        else:
            ports.update(f"{match[1]}{n}" for n in range(int(match[2]), int(match[3]) + 1))
    ports.discard("")
    return ports


@needs_device
def test_get_vlans(shared: CiscoNXOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        vlans = shared.get_vlans()
    # No VLAN names: this output gets pasted back for review.
    print(f"\n{len(vlans)} VLANs")
    print(f"{'id':>5} {'active':<6} {'status':<10} {'shut':<11} access ports")
    for v in vlans:
        shut = v.shut_state or "-"
        print(f"{v.vlan_id:>5} {v.active!s:<6} {v.status:<10} {shut:<11} {len(v.interfaces)}")
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    # Reported, not asserted: compare with the port lists of `show vlan brief`
    # where they are intact (7.3 damages long ones, D41). Those lists hold
    # trunk ports too, so only ports get_switchports() calls access count.
    raw = shared.get_vlans_raw()
    listed = rows(
        _json(shared, SHOW_VLAN_BRIEF, raw[SHOW_VLAN_BRIEF]),
        "TABLE_vlanbriefxbrief",
        "ROW_vlanbriefxbrief",
        SHOW_VLAN_BRIEF,
    )
    access = {p.interface for p in shared.get_switchports() if p.mode == "access"}
    ours = {v.vlan_id: set(v.interfaces) for v in vlans}
    damaged = missing = extra = 0
    for row in listed:
        vid = int(text(row, "vlanshowbr-vlanid-utf") or text(row, "vlanshowbr-vlanid") or 0)
        ports = _listed_ports(row.get("vlanshowplist-ifidx"))
        if ports is None:
            damaged += 1
            continue
        theirs = ports & access
        missing += len(theirs - ours.get(vid, set()))
        extra += len(ours.get(vid, set()) - theirs)
    print(f"VLANs with a damaged port list in show vlan brief: {damaged}")
    print(f"access ports vs intact show vlan brief lists: {missing} missing, {extra} extra here")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({p for v in vlans for p in v.interfaces} - interface_names)
    assert not unknown, f"VLAN ports that get_interfaces() doesn't list: {unknown}"


def _allowed_as_printed(value: str) -> tuple[int, ...] | str:
    if value == "1-4094":
        return "ALL"
    if value in ("", "none"):
        return ()
    return vlan_ids(value)


@needs_device
def test_get_switchports(shared: CiscoNXOS) -> None:
    ports = shared.get_switchports()
    print(f"\n{len(ports)} switchports")
    print(f"{'interface':<22} {'mode':<6} {'oper mode':<12} {'access':>6} {'native':>6} allowed")
    for p in ports:
        if p.all_vlans_allowed:
            allowed = "ALL"
        elif p.allowed_vlans is None:
            allowed = "-"
        else:
            allowed = f"{len(p.allowed_vlans)} VLANs"
        print(
            f"{p.interface:<22} {p.mode or '-':<6} {p.oper_mode or '-':<12} "
            f"{p.access_vlan or '-':>6} {p.native_vlan or '-':>6} {allowed}"
        )
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({p.interface for p in ports} - interface_names)
    assert not unknown, f"switchports that get_interfaces() doesn't list: {unknown}"

    # Cross-check (D41): each trunk's allowed list against `show interface trunk`.
    trunk = _json(shared, SHOW_INTERFACE_TRUNK, shared.send_command(SHOW_INTERFACE_TRUNK))
    table = trunk.get("TABLE_allowed_vlans")
    # 9.3/10.5: {ROW_allowed_vlans: [rows]}; 7.3(11)N1(1): a list of
    # {ROW_allowed_vlans: row}, without interface names (live, 2026-11-18).
    entries = table if isinstance(table, list) else [table] if isinstance(table, dict) else []
    allowed_rows = [
        r
        for e in entries
        for v in e.values()
        for r in (v if isinstance(v, list) else [v])
        if isinstance(r, dict)
    ]
    if allowed_rows and not all("interface" in r for r in allowed_rows):
        print(
            "show interface trunk names no interfaces in its allowed-VLAN rows: cross-check skipped"
        )
        return
    there: dict[str, str] = {
        text(r, "interface") or "": text(r, "allowedvlans") or "" for r in allowed_rows
    }
    differ = []
    for p in ports:
        if p.interface not in there:
            continue
        value = there[p.interface]
        theirs = _allowed_as_printed(value)
        ours: tuple[int, ...] | str | None = "ALL" if p.all_vlans_allowed else p.allowed_vlans
        if ours != theirs:
            differ.append(p.interface)
    longest = max((len(v) for v in there.values()), default=0)
    print(f"trunks in show interface trunk: {len(there)}, longest allowed list {longest} chars")
    print(f"allowed lists that differ from show interface trunk: {differ or 'none'}")
    assert not differ


@needs_device
def test_get_vtp_status(shared: CiscoNXOS) -> None:
    try:
        vtp = shared.get_vtp_status()
    except NotSupportedError as exc:
        pytest.skip(f"no VTP: {exc}")
    # No domain name or addresses: this output gets pasted back for review.
    shown = vtp.model_dump(exclude={"domain_name", "last_modified_by", "updater_id", "md5_digest"})
    print("\n" + "\n".join(f"{name}: {value}" for name, value in shown.items()))
    for name in ("domain_name", "last_modified_by", "updater_id", "md5_digest"):
        print(f"{name}: {'set' if getattr(vtp, name) is not None else 'None'}")
    # Reported, not asserted: VTP's VLAN count against get_vlans().
    print(f"vlan_count {vtp.vlan_count}, get_vlans() {len(shared.get_vlans())}")
    assert vtp.mode


@needs_device
def test_get_neighbors(shared: CiscoNXOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="netsdk"):
        try:
            neighbors = shared.get_neighbors()
        except NotSupportedError:
            pytest.skip("switch has neither CDP nor LLDP")
    by_protocol = {p: sum(n.protocol == p for n in neighbors) for p in ("cdp", "lldp")}
    print(f"\n{len(neighbors)} neighbors: {by_protocol['cdp']} CDP, {by_protocol['lldp']} LLDP")
    # No names, addresses, platforms or descriptions: this output gets pasted back.
    print(f"{'proto':<5} {'local interface':<22} {'remote port':<24} {'name':<4} {'addr':<4} caps")
    for n in neighbors:
        print(
            f"{n.protocol:<5} {n.local_interface or '-':<22} {n.remote_port or '-':<24} "
            f"{'set' if n.remote_name else '-':<4} {len(n.management_addresses):<4} "
            f"{','.join(n.capabilities) or '-'}"
        )
    for record in caplog.records:
        if record.name.startswith("netsdk.cisco"):
            print(f"{record.levelname}: {record.getMessage()}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({n.local_interface for n in neighbors if n.local_interface} - interface_names)
    assert not unknown, f"local interfaces that get_interfaces() doesn't list: {unknown}"


@needs_device
def test_shared_session_log_is_complete_and_clean(shared: CiscoNXOS) -> None:
    # Last shared test: logs out, then checks the whole session's log.
    shared.close()
    log = shared.session_log
    assert log is not None, "device has no session log"
    assert log.exists(), "session log was not written"
    data = log.read_bytes().decode("utf-8", "replace")
    for secret in (PASSWORD, KEY_PASSPHRASE):
        if secret:
            assert secret not in data, "a secret leaked into the session log"
    print(f"\nsession log: {log}")
    print(f"pager prompts answered: {data.count('--More--')}")
    assert "--More--" in data
    assert "exit" in data[-200:], "the log doesn't end with our logout"


# -- failures ------------------------------------------------------------------


@needs_device
@pytest.mark.skipif(not AUTH_FAILURE_TEST, reason="set NETSDK_LIVE_AUTH_FAILURE=1 to run")
def test_wrong_password_raises_authentication_error() -> None:
    assert USERNAME
    creds = Credentials(username=USERNAME, password=SecretStr("netsdk-wrong-password"))
    with pytest.raises(AuthenticationError):
        device(creds).open()


def test_unreachable_host_raises_connection_error() -> None:
    # Never reaches a device, so it can't affect AAA.
    creds = Credentials(username=USERNAME or "nobody", password=SecretStr("x"))
    with pytest.raises(DeviceConnectionError) as excinfo:
        device(creds, host=UNREACHABLE_HOST, timeout=5.0).open()
    assert not isinstance(excinfo.value, AuthenticationError)
