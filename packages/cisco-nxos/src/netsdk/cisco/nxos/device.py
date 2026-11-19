"""Cisco NX-OS device."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from typing import ClassVar

from netsdk.cisco.nxos.commands import (
    SHOW_CDP_NEIGHBORS_DETAIL,
    SHOW_INTERFACE,
    SHOW_INTERFACE_SWITCHPORT,
    SHOW_INVENTORY,
    SHOW_IP_INTERFACE,
    SHOW_LLDP_NEIGHBORS_DETAIL,
    SHOW_PORT_CHANNEL_SUMMARY,
    SHOW_VERSION,
    SHOW_VLAN_BRIEF,
    SHOW_VPC,
    SHOW_VTP_STATUS,
)
from netsdk.cisco.nxos.models import (
    NXOSVLAN,
    NXOSFacts,
    NXOSInterface,
    NXOSNeighbor,
    NXOSPortChannel,
    NXOSSwitchport,
    NXOSVPCStatus,
    NXOSVTPStatus,
)
from netsdk.cisco.nxos.parsers import (
    parse_facts,
    parse_show_cdp_neighbors_detail,
    parse_show_interface,
    parse_show_interface_switchport,
    parse_show_inventory,
    parse_show_ip_interface,
    parse_show_lldp_neighbors_detail,
    parse_show_port_channel_summary,
    parse_show_vlan_brief,
    parse_show_vpc,
    parse_show_vtp_status,
)
from netsdk.cisco.nxos.transport import NXOS_TERMINAL_WIDTH, NXOSSSHTransport
from netsdk.core import (
    CommandError,
    Credentials,
    Device,
    DeviceConnectionError,
    InventoryItem,
    IPAddress,
    NetSDKError,
    NotSupportedError,
)
from netsdk.core.device import DEFAULT_SESSION_LOG_DIR
from netsdk.core.transports.ssh import (
    DEFAULT_KNOWN_HOSTS,
    DEFAULT_TERMINAL_HEIGHT,
)

log = logging.getLogger(__name__)

#: How NX-OS rejects a command that doesn't exist, e.g. without its feature.
_REJECTED = "% Invalid command"


class CiscoNXOS(Device):
    """A Cisco Nexus switch running standalone NX-OS (not ACI), reached over SSH."""

    transport_class: ClassVar[type[NXOSSSHTransport]] = NXOSSSHTransport

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
        terminal_width: int = NXOS_TERMINAL_WIDTH,
        terminal_height: int = DEFAULT_TERMINAL_HEIGHT,
    ) -> None:
        super().__init__(
            host, credentials, port=port, timeout=timeout, session_log_dir=session_log_dir
        )
        self.known_hosts_file = known_hosts_file
        self.strict_host_keys = strict_host_keys
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height
        self._transport: NXOSSSHTransport | None = None
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

    def get_facts(self) -> NXOSFacts:
        """Hostname, chassis part ID and serial, OS version and uptime."""
        raw = self.get_facts_raw()
        with self._attributed():
            return parse_facts(
                raw[SHOW_VERSION], raw[SHOW_INVENTORY], wrap_width=self.terminal_width
            )

    def get_facts_raw(self) -> dict[str, str]:
        return {command: self.send_command(command) for command in (SHOW_VERSION, SHOW_INVENTORY)}

    def get_interfaces(self) -> list[NXOSInterface]:
        """Every interface with admin/oper state, MAC, MTU, speed and duplex."""
        raw = self.get_interfaces_raw()
        with self._attributed():
            return parse_show_interface(raw[SHOW_INTERFACE], wrap_width=self.terminal_width)

    def get_interfaces_raw(self) -> dict[str, str]:
        return {SHOW_INTERFACE: self.send_command(SHOW_INTERFACE)}

    def get_ip_addresses(self) -> list[IPAddress]:
        """IPv4 addresses (primary and secondary) with their interface and VRF.

        Every VRF, `management` included; `default` is reported as None (the
        global table). Unnumbered interfaces have none. IPv6 is not collected.
        """
        raw = self.get_ip_addresses_raw()
        with self._attributed():
            return parse_show_ip_interface(raw[SHOW_IP_INTERFACE], wrap_width=self.terminal_width)

    def get_ip_addresses_raw(self) -> dict[str, str]:
        return {SHOW_IP_INTERFACE: self.send_command(SHOW_IP_INTERFACE)}

    def get_vlans(self) -> list[NXOSVLAN]:
        """VLANs with name, state and access ports (trunk ports: get_switchports())."""
        raw = self.get_vlans_raw()
        with self._attributed():
            return parse_show_vlan_brief(
                raw[SHOW_VLAN_BRIEF],
                raw[SHOW_INTERFACE_SWITCHPORT],
                wrap_width=self.terminal_width,
            )

    def get_vlans_raw(self) -> dict[str, str]:
        """`show vlan brief`, and `show interface switchport` to tell access ports (D41)."""
        return {
            command: self.send_command(command)
            for command in (SHOW_VLAN_BRIEF, SHOW_INTERFACE_SWITCHPORT)
        }

    def get_switchports(self) -> list[NXOSSwitchport]:
        """Layer-2 ports: mode, access/native VLAN and allowed VLANs (1-4094 is "all")."""
        raw = self.get_switchports_raw()
        with self._attributed():
            return parse_show_interface_switchport(
                raw[SHOW_INTERFACE_SWITCHPORT], wrap_width=self.terminal_width
            )

    def get_switchports_raw(self) -> dict[str, str]:
        return {SHOW_INTERFACE_SWITCHPORT: self.send_command(SHOW_INTERFACE_SWITCHPORT)}

    def get_vtp_status(self) -> NXOSVTPStatus:
        """VTP version, domain, mode, revision, pruning and MD5 digest.

        Raises NotSupportedError when the switch rejects the command
        (`feature vtp` not enabled).
        """
        raw = self.get_vtp_status_raw()
        with self._attributed():
            return parse_show_vtp_status(raw[SHOW_VTP_STATUS], wrap_width=self.terminal_width)

    def get_vtp_status_raw(self) -> dict[str, str]:
        reason = "VTP is not enabled, or this release has no JSON for it"
        return {SHOW_VTP_STATUS: self._send_if_supported(SHOW_VTP_STATUS, reason)}

    def _send_if_supported(self, command: str, reason: str) -> str:
        try:
            return self.send_command(command)
        except CommandError as exc:
            # Only "% Invalid command" (the command doesn't exist without its
            # feature); an authorization failure stays an error.
            if exc.output and _REJECTED in exc.output:
                raise NotSupportedError(
                    f"{command!r} is not available: {reason}", host=self.host
                ) from exc
            raise

    def get_neighbors(self) -> list[NXOSNeighbor]:
        """CDP neighbors, then LLDP neighbors, each tagged with its protocol.

        A protocol that is off (its command rejected, e.g. `feature lldp` not
        enabled) contributes no neighbors and is logged; NotSupportedError
        only when both are (D44).
        """
        raw = self.get_neighbors_raw()
        off = [command for command, output in raw.items() if _REJECTED in output]
        if len(off) == len(raw):
            raise NotSupportedError("neither CDP nor LLDP is enabled", host=self.host)
        for command in off:
            log.info("%s: %r rejected; that protocol is off", self.host, command)
        cdp, lldp = raw[SHOW_CDP_NEIGHBORS_DETAIL], raw[SHOW_LLDP_NEIGHBORS_DETAIL]
        width = self.terminal_width
        with self._attributed():
            neighbors = (
                []
                if SHOW_CDP_NEIGHBORS_DETAIL in off
                else (parse_show_cdp_neighbors_detail(cdp, wrap_width=width))
            )
            if SHOW_LLDP_NEIGHBORS_DETAIL not in off:
                neighbors += parse_show_lldp_neighbors_detail(lldp, wrap_width=width)
            return neighbors

    def get_neighbors_raw(self) -> dict[str, str]:
        """Both commands' output; a rejected command's message is kept, not raised."""
        return {
            command: self._send_keeping_rejection(command)
            for command in (SHOW_CDP_NEIGHBORS_DETAIL, SHOW_LLDP_NEIGHBORS_DETAIL)
        }

    def _send_keeping_rejection(self, command: str) -> str:
        try:
            return self.send_command(command)
        except CommandError as exc:
            # Only "% Invalid command"; an authorization failure stays an error.
            if exc.output and _REJECTED in exc.output:
                return exc.output
            raise

    def get_inventory(self) -> list[InventoryItem]:
        """Every inventory item in device order: name, description, part ID, version ID, serial."""
        raw = self.get_inventory_raw()
        with self._attributed():
            return parse_show_inventory(raw[SHOW_INVENTORY], wrap_width=self.terminal_width)

    def get_inventory_raw(self) -> dict[str, str]:
        return {SHOW_INVENTORY: self.send_command(SHOW_INVENTORY)}

    def get_port_channels(self) -> list[NXOSPortChannel]:
        """Port-channels with protocol, state and each member's bundled state (D46)."""
        raw = self.get_port_channels_raw()
        with self._attributed():
            return parse_show_port_channel_summary(
                raw[SHOW_PORT_CHANNEL_SUMMARY], wrap_width=self.terminal_width
            )

    def get_port_channels_raw(self) -> dict[str, str]:
        return {SHOW_PORT_CHANNEL_SUMMARY: self.send_command(SHOW_PORT_CHANNEL_SUMMARY)}

    def get_vpc_status(self) -> NXOSVPCStatus:
        """vPC domain, role, peer-link and vPCs, with peer, keepalive and
        consistency health flags (D47).

        Raises NotSupportedError when the switch rejects the command (vPC off).
        """
        raw = self.get_vpc_status_raw()
        with self._attributed():
            return parse_show_vpc(raw[SHOW_VPC], wrap_width=self.terminal_width)

    def get_vpc_status_raw(self) -> dict[str, str]:
        return {SHOW_VPC: self._send_if_supported(SHOW_VPC, "vPC is not enabled")}

    @contextmanager
    def _attributed(self) -> Iterator[None]:
        # Parsers don't know the host; add it so batch errors stay attributable.
        try:
            yield
        except NetSDKError as exc:
            if exc.host is None:
                exc.host = self.host
            raise
