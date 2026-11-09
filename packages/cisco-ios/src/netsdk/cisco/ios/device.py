"""Cisco IOS device."""

from os import PathLike
from pathlib import Path
from typing import ClassVar

from netsdk.cisco.ios.transport import IOSSSHTransport
from netsdk.core import Credentials, Device, DeviceConnectionError
from netsdk.core.device import DEFAULT_SESSION_LOG_DIR
from netsdk.core.transports.ssh import DEFAULT_KNOWN_HOSTS


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
    ) -> None:
        super().__init__(
            host, credentials, port=port, timeout=timeout, session_log_dir=session_log_dir
        )
        self.known_hosts_file = known_hosts_file
        self.strict_host_keys = strict_host_keys
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
