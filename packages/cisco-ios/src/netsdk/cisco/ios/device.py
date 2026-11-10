"""Cisco IOS device."""

from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from typing import ClassVar

from netsdk.cisco.ios.commands import (
    SHOW_INTERFACES,
    SHOW_IP_INTERFACE,
    SHOW_VERSION,
    SHOW_VLAN_BRIEF,
)
from netsdk.cisco.ios.models import IOSVLAN, IOSFacts, IOSInterface
from netsdk.cisco.ios.parsers import (
    parse_show_interfaces,
    parse_show_ip_interface,
    parse_show_version,
    parse_show_vlan_brief,
)
from netsdk.cisco.ios.transport import IOSSSHTransport
from netsdk.core import (
    CommandError,
    Credentials,
    Device,
    DeviceConnectionError,
    IPAddress,
    NetSDKError,
    NotSupportedError,
)
from netsdk.core.device import DEFAULT_SESSION_LOG_DIR
from netsdk.core.transports.ssh import (
    DEFAULT_KNOWN_HOSTS,
    DEFAULT_TERMINAL_HEIGHT,
    DEFAULT_TERMINAL_WIDTH,
)


class CiscoIOS(Device):
    """A Cisco IOS router or switch, reached over SSH."""

    transport_class: ClassVar[type[IOSSSHTransport]] = IOSSSHTransport

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        *,
        port: int = 22,
        timeout: float = 30.0,
        session_log_dir: str | PathLike[str] = DEFAULT_SESSION_LOG_DIR,
        known_hosts_file: Path | None = DEFAULT_KNOWN_HOSTS,
        strict_host_keys: bool = False,
        terminal_width: int = DEFAULT_TERMINAL_WIDTH,
        terminal_height: int = DEFAULT_TERMINAL_HEIGHT,
    ) -> None:
        super().__init__(
            host, credentials, port=port, timeout=timeout, session_log_dir=session_log_dir
        )
        self.known_hosts_file = known_hosts_file
        self.strict_host_keys = strict_host_keys
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height
        self._transport: IOSSSHTransport | None = None
        self._session_log: Path | None = None

    @property
    def is_open(self) -> bool:
        return self._transport is not None and self._transport.is_open

    def open(self) -> None:
        if self.is_open:
            return
        # Remembered before connecting: a failed login's log is the useful one.
        self._session_log = self.new_session_log_path()
        transport = self.transport_class(
            self.host,
            self.credentials,
            session_log=self._session_log,
            port=self.port or 22,
            timeout=self.timeout,
            known_hosts_file=self.known_hosts_file,
            strict_host_keys=self.strict_host_keys,
            terminal_width=self.terminal_width,
            terminal_height=self.terminal_height,
        )
        transport.open()
        self._transport = transport

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    @property
    def session_log(self) -> Path | None:
        """Log file of the current or most recent session, also after close()
        or a failed open(). None until open() is first called."""
        return self._session_log

    def send_command(self, command: str, *, timeout: float | None = None) -> str:
        """Run one CLI command and return its raw text output.

        The raw escape hatch for anything getters don't cover. Output has the
        command echo, prompt and pager prompts removed.
        """
        if self._transport is None or not self._transport.is_open:
            raise DeviceConnectionError(
                "device is not open; call open() or use it as a context manager", host=self.host
            )
        return self._transport.send_command(command, timeout=timeout)

    # -- getters ------------------------------------------------------------

    def get_facts(self) -> IOSFacts:
        """Hostname, model, serial number, OS version and uptime."""
        raw = self.get_facts_raw()
        with self._attributed():
            return parse_show_version(raw[SHOW_VERSION])

    def get_facts_raw(self) -> dict[str, str]:
        return {SHOW_VERSION: self.send_command(SHOW_VERSION)}

    def get_interfaces(self) -> list[IOSInterface]:
        """Every interface with admin/oper state, MAC, MTU, speed and duplex."""
        raw = self.get_interfaces_raw()
        with self._attributed():
            return parse_show_interfaces(raw[SHOW_INTERFACES])

    def get_interfaces_raw(self) -> dict[str, str]:
        return {SHOW_INTERFACES: self.send_command(SHOW_INTERFACES)}

    def get_ip_addresses(self) -> list[IPAddress]:
        """IPv4 addresses (primary and secondary) with their interface and VRF.

        IPv6 is not collected yet: the model supports it, the getter doesn't.
        """
        raw = self.get_ip_addresses_raw()
        with self._attributed():
            return parse_show_ip_interface(raw[SHOW_IP_INTERFACE])

    def get_ip_addresses_raw(self) -> dict[str, str]:
        return {SHOW_IP_INTERFACE: self.send_command(SHOW_IP_INTERFACE)}

    def get_vlans(self) -> list[IOSVLAN]:
        """VLANs with their access ports (full names). Trunks are not included.

        Raises NotSupportedError on a device without VLANs (e.g. a router
        without switch ports), so that's distinct from "no VLANs".
        """
        raw = self.get_vlans_raw()
        with self._attributed():
            return parse_show_vlan_brief(raw[SHOW_VLAN_BRIEF])

    def get_vlans_raw(self) -> dict[str, str]:
        try:
            return {SHOW_VLAN_BRIEF: self.send_command(SHOW_VLAN_BRIEF)}
        except CommandError as exc:
            # Only "% Invalid input": an authorization failure stays an error.
            if exc.output and "% Invalid input" in exc.output:
                raise NotSupportedError(
                    f"{SHOW_VLAN_BRIEF!r} is not available: no VLAN support", host=self.host
                ) from exc
            raise

    @contextmanager
    def _attributed(self) -> Iterator[None]:
        # Parsers don't know the host; add it so batch errors stay attributable.
        try:
            yield
        except NetSDKError as exc:
            if exc.host is None:
                exc.host = self.host
            raise
