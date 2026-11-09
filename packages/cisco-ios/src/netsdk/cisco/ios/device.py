"""Cisco IOS device."""

from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from typing import ClassVar

from netsdk.cisco.ios.commands import SHOW_INTERFACES, SHOW_VERSION
from netsdk.cisco.ios.models import IOSFacts, IOSInterface
from netsdk.cisco.ios.parsers import parse_show_interfaces, parse_show_version
from netsdk.cisco.ios.transport import IOSSSHTransport
from netsdk.core import Credentials, Device, DeviceConnectionError, NetSDKError
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

    @property
    def is_open(self) -> bool:
        return self._transport is not None and self._transport.is_open

    def open(self) -> None:
        if self.is_open:
            return
        transport = self.transport_class(
            self.host,
            self.credentials,
            session_log=self.new_session_log_path(),
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
        """Log file of the current (or last opened) session."""
        return self._transport.session_log if self._transport is not None else None

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

    @contextmanager
    def _attributed(self) -> Iterator[None]:
        # Parsers don't know the host; add it so batch errors stay attributable.
        try:
            yield
        except NetSDKError as exc:
            if exc.host is None:
                exc.host = self.host
            raise
