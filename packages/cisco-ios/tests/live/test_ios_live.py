"""Live tests against a real IOS device. Run manually: `pytest -m live -s`.

Configuration comes from environment variables, never from code. Set a
password, a key, or both; tests log in with whatever is configured. To test
one auth method on its own, set only that one for the run.

    NETSDK_IOS_HOST           device to test (required)
    NETSDK_IOS_PORT           SSH port (default 22)
    NETSDK_USERNAME           read-only account (required)
    NETSDK_PASSWORD           password
    NETSDK_KEY_FILE           private key path (OpenSSH format, not PuTTY .ppk)
    NETSDK_KEY_PASSPHRASE     key passphrase, if the key has one
    NETSDK_IOS_LONG_COMMAND   command long enough to page (default "show interfaces")
    NETSDK_UNREACHABLE_HOST   host that drops SSH (default 192.0.2.1, TEST-NET-1)
    NETSDK_SESSION_LOG_DIR    where session logs go (default "session_logs")
    NETSDK_LIVE_AUTH_FAILURE  set to 1 to run the deliberate failed-login test
                              (off by default: it can count towards AAA lockout)

Almost everything runs in one shared session: devices limit concurrent SSH
sessions (often 5 vty lines). Only the vty diagnostic and the failure tests
open their own.
"""

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from netsdk.cisco.ios import CiscoIOS
from netsdk.core import (
    AuthenticationError,
    CommandError,
    Credentials,
    DeviceConnectionError,
    IPAddress,
    NotSupportedError,
)

pytestmark = pytest.mark.live


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


HOST = _env("NETSDK_IOS_HOST")
PORT = int(_env("NETSDK_IOS_PORT") or 22)
USERNAME = _env("NETSDK_USERNAME")
PASSWORD = _env("NETSDK_PASSWORD")
KEY_FILE = _env("NETSDK_KEY_FILE")
KEY_PASSPHRASE = _env("NETSDK_KEY_PASSPHRASE")
LONG_COMMAND = _env("NETSDK_IOS_LONG_COMMAND") or "show interfaces"
UNREACHABLE_HOST = _env("NETSDK_UNREACHABLE_HOST") or "192.0.2.1"
LOG_DIR = Path(_env("NETSDK_SESSION_LOG_DIR") or "session_logs")
AUTH_FAILURE_TEST = _env("NETSDK_LIVE_AUTH_FAILURE") == "1"

#: The shared session uses a 24-line terminal so IOS pages every ~23 lines and
#: every test exercises the pager (the default 1000-line terminal rarely pages).
PAGED_HEIGHT = 24

needs_device = pytest.mark.skipif(
    not (HOST and USERNAME and (PASSWORD or KEY_FILE)),
    reason="set NETSDK_IOS_HOST, NETSDK_USERNAME and NETSDK_PASSWORD and/or NETSDK_KEY_FILE",
)


def configured_creds() -> Credentials:
    """Whatever is configured: password, key, or both."""
    assert USERNAME
    return Credentials(
        username=USERNAME,
        password=SecretStr(PASSWORD) if PASSWORD else None,
        key_file=Path(KEY_FILE) if KEY_FILE else None,
        key_passphrase=SecretStr(KEY_PASSPHRASE) if KEY_PASSPHRASE else None,
    )


def device(
    creds: Credentials, host: str | None = None, timeout: float = 30.0, **kwargs: Any
) -> CiscoIOS:
    target = host or HOST
    assert target
    return CiscoIOS(target, creds, port=PORT, timeout=timeout, session_log_dir=LOG_DIR, **kwargs)


def assert_log_is_clean(log: Path | None) -> None:
    assert log is not None, "device has no session log"
    assert log.exists(), "session log was not written"
    text = log.read_bytes().decode("utf-8", "replace")
    for secret in (PASSWORD, KEY_PASSPHRASE):
        if secret:
            assert secret not in text, "a secret leaked into the session log"
    print(f"\nsession log: {log}")


@pytest.fixture(scope="module")
def shared() -> Iterator[CiscoIOS]:
    with device(configured_creds(), timeout=120.0, terminal_height=PAGED_HEIGHT) as dev:
        yield dev


# -- session behaviour -----------------------------------------------------------


@needs_device
def test_login_and_show_version(shared: CiscoIOS) -> None:
    methods = [name for name, value in (("password", PASSWORD), ("key", KEY_FILE)) if value]
    output = shared.send_command("show version")
    print(f"\nlogged in with: {' or '.join(methods)}")
    assert "Cisco IOS" in output


@needs_device
def test_show_terminal(shared: CiscoIOS) -> None:
    # IOS takes width/length from the PTY we request.
    output = shared.send_command("show terminal")
    sizes = [line for line in output.splitlines() if "Length" in line or "Width" in line]
    print("\n" + "\n".join(sizes))
    assert f"Length: {PAGED_HEIGHT} lines" in output


@needs_device
def test_long_output_is_paged_cleanly(shared: CiscoIOS) -> None:
    output = shared.send_command(LONG_COMMAND)
    lines = output.splitlines()
    print(f"\n{LONG_COMMAND!r}: {len(lines)} lines on a {PAGED_HEIGHT}-line terminal")
    print("last 5 lines:\n" + "\n".join(lines[-5:]))
    assert len(lines) > PAGED_HEIGHT, "output fits one page; set NETSDK_IOS_LONG_COMMAND"
    assert "--More--" not in output
    assert "\b" not in output
    assert "\r" not in output


@needs_device
def test_rejected_command_raises_and_session_survives(shared: CiscoIOS) -> None:
    with pytest.raises(CommandError) as excinfo:
        shared.send_command("show netsdk-no-such-command")
    print(f"\nrejected as: {excinfo.value}")
    assert shared.send_command("show clock")


@needs_device
def test_closed_session_frees_its_vty_line(shared: CiscoIOS) -> None:
    # Diagnostic: counts vty lines in use (no names or addresses printed).
    # Opens the only second session of the run.
    def vty_lines() -> int:
        try:
            output = shared.send_command("show users")
        except CommandError:
            pytest.skip("`show users` is not permitted for this account")
        return sum(" vty " in line for line in output.splitlines())

    before = vty_lines()
    with device(configured_creds()) as extra:
        during = vty_lines()
        extra.send_command("show clock")
    after = [vty_lines()]
    for _ in range(3):
        time.sleep(5)
        after.append(vty_lines())
    print(
        f"\nvty lines in use: before {before}, during {during}, after close (0/5/10/15 s) {after}"
    )
    assert during == before + 1, "the extra session didn't show up in `show users`"
    assert after[-1] == before, "the closed session still holds a vty line after 15 s"


# -- getters -------------------------------------------------------------------


@needs_device
def test_get_facts(shared: CiscoIOS) -> None:
    facts = shared.get_facts()
    print("\n" + "\n".join(f"{name}: {value}" for name, value in facts.model_dump().items()))
    assert facts.vendor == "Cisco"


@needs_device
def test_get_interfaces(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        interfaces = shared.get_interfaces()
    print(f"\n{len(interfaces)} interfaces")
    # No MACs or descriptions: this output gets pasted back for review.
    print(f"{'name':<28} {'en':<3} {'up':<3} {'speed':>10} {'duplex':<6} {'mtu':>5}")
    for i in interfaces:
        print(
            f"{i.name:<28} {i.enabled!s:<3.1} {i.oper_up!s:<3.1} {i.speed or '-':>10} "
            f"{i.duplex or '-':<6} {i.mtu or '-':>5}"
        )
    with_mac = sum(i.mac_address is not None for i in interfaces)
    print(f"{with_mac} of {len(interfaces)} have a MAC address")
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    assert interfaces


@needs_device
def test_get_ip_addresses(shared: CiscoIOS) -> None:
    addresses = shared.get_ip_addresses()
    # No addresses or VRF names: this output gets pasted back for review.
    vrfs = sorted({a.vrf for a in addresses if a.vrf})
    print(f"\n{len(addresses)} addresses in {len(vrfs)} VRF(s) plus the global table")
    print(f"{'interface':<28} {'prefixes':<16} {'secondary':<9} {'vrf'}")
    by_interface: dict[str, list[IPAddress]] = {}
    for address in addresses:
        by_interface.setdefault(address.interface, []).append(address)
    for name, records in by_interface.items():
        prefixes = ",".join(f"/{a.address.network.prefixlen}" for a in records)
        secondary = "yes" if any(a.secondary for a in records) else "-"
        vrf = f"VRF {vrfs.index(records[0].vrf) + 1}" if records[0].vrf else "global"
        print(f"{name:<28} {prefixes:<16} {secondary:<9} {vrf}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted(set(by_interface) - interface_names)
    assert not unknown, f"addresses on interfaces get_interfaces() doesn't list: {unknown}"


@needs_device
def test_get_vlans(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        try:
            vlans = shared.get_vlans()
        except NotSupportedError:
            pytest.skip("device has no VLANs (router?)")
    # No VLAN names: this output gets pasted back for review.
    print(f"\n{len(vlans)} VLANs")
    print(f"{'id':>5} {'active':<6} {'status':<10} {'access ports'}")
    for v in vlans:
        print(f"{v.vlan_id:>5} {v.active!s:<6} {v.status:<10} {len(v.interfaces)}")
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({p for v in vlans for p in v.interfaces} - interface_names)
    assert not unknown, f"VLAN ports that get_interfaces() doesn't list: {unknown}"


@needs_device
def test_get_switchports(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        try:
            ports = shared.get_switchports()
        except NotSupportedError:
            pytest.skip("device has no switch ports (router?)")
    print(f"\n{len(ports)} switchports")
    print(
        f"{'interface':<28} {'mode':<6} {'admin mode':<18} {'oper mode':<14} "
        f"{'access':>6} {'native':>6} {'voice':>5} {'allowed'}"
    )
    for p in sorted(ports, key=lambda p: p.interface):
        if p.all_vlans_allowed:
            allowed = "ALL"
        elif p.allowed_vlans is None:
            allowed = "-"
        else:
            allowed = f"{len(p.allowed_vlans)} VLANs"
        print(
            f"{p.interface:<28} {p.mode or '-':<6} {p.admin_mode:<18} {p.oper_mode or '-':<14} "
            f"{p.access_vlan or '-':>6} {p.native_vlan or '-':>6} {p.voice_vlan or '-':>5} "
            f"{allowed}"
        )
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({p.interface for p in ports} - interface_names)
    assert not unknown, f"switchports that get_interfaces() doesn't list: {unknown}"
    # Reported, not asserted: access ports `show vlan brief` puts in another
    # VLAN than their access VLAN (or leaves out).
    try:
        vlan_of = {name: v.vlan_id for v in shared.get_vlans() for name in v.interfaces}
    except NotSupportedError:
        return
    differ = [
        f"{p.interface} (access {p.access_vlan}, get_vlans {vlan_of.get(p.interface)})"
        for p in ports
        if p.mode == "access" and vlan_of.get(p.interface) != p.access_vlan
    ]
    print(f"access ports whose VLAN differs from get_vlans(): {differ or 'none'}")


@needs_device
def test_get_neighbors(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="netsdk"):
        try:
            neighbors = shared.get_neighbors()
        except NotSupportedError:
            pytest.skip("device has neither CDP nor LLDP")
    by_protocol = {p: sum(n.protocol == p for n in neighbors) for p in ("cdp", "lldp")}
    print(f"\n{len(neighbors)} neighbors: {by_protocol['cdp']} CDP, {by_protocol['lldp']} LLDP")
    # No names, addresses, platforms or descriptions: this output gets pasted back.
    print(f"{'proto':<5} {'local interface':<28} {'remote port':<28} {'name':<4} {'addr':<4} caps")
    for n in neighbors:
        print(
            f"{n.protocol:<5} {n.local_interface or '-':<28} {n.remote_port or '-':<28} "
            f"{'set' if n.remote_name else '-':<4} {len(n.management_addresses):<4} "
            f"{','.join(n.capabilities) or '-'}"
        )
    for record in caplog.records:
        print(f"{record.levelname}: {record.getMessage()}")
    interface_names = {i.name for i in shared.get_interfaces()}
    unknown = sorted({n.local_interface for n in neighbors if n.local_interface} - interface_names)
    assert not unknown, f"local interfaces that get_interfaces() doesn't list: {unknown}"


@needs_device
def test_get_vtp_status(shared: CiscoIOS, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        try:
            vtp = shared.get_vtp_status()
        except NotSupportedError:
            pytest.skip("device has no VTP (router?)")
    # No domain, IDs, addresses or descriptions: this output gets pasted back.
    print(f"\nversion {vtp.version} (max {vtp.max_version or '-'}), mode {vtp.mode}")
    print(f"revision {vtp.configuration_revision}, pruning {vtp.pruning_enabled}")
    print(
        f"VLANs {vtp.vlan_count} (extended {vtp.extended_vlan_count}, max {vtp.max_vlans}), "
        f"traps {vtp.traps_enabled}"
    )
    is_set = {
        name: "set" if getattr(vtp, name) is not None else "-"
        for name in (
            "domain_name",
            "device_id",
            "primary_id",
            "primary_description",
            "last_modified_by",
            "last_modified_at",
            "updater_id",
            "updater_interface",
        )
    }
    print(", ".join(f"{name} {value}" for name, value in is_set.items()))
    for record in caplog.records:
        print(f"WARNING: {record.getMessage()}")
    # Reported, not asserted: whether VTP counts every VLAN `show vlan brief`
    # lists (1002-1005, extended range) may differ by platform.
    try:
        listed = len(shared.get_vlans())
    except NotSupportedError:
        listed = None
    counted = vtp.vlan_count + (vtp.extended_vlan_count or 0)
    print(f"VLAN count: VTP {counted}, get_vlans() {listed}")
    if vtp.updater_interface:
        interface_names = {i.name for i in shared.get_interfaces()}
        assert vtp.updater_interface in interface_names, "updater interface not in get_interfaces()"


@needs_device
def test_shared_session_log_is_complete_and_clean(shared: CiscoIOS) -> None:
    # Last shared test: logs out, then checks the whole session's log.
    shared.close()
    log = shared.session_log
    assert_log_is_clean(log)
    assert log is not None
    text = log.read_bytes().decode("utf-8", "replace")
    print(f"pager prompts answered: {text.count('--More--')}")
    assert "--More--" in text
    assert "exit" in text[-200:], "the log doesn't end with our logout"


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
