"""netmiko-based SSH transport that sends only what it is asked to send.

netmiko's own drivers prepare a session by sending `terminal width`,
`terminal length 0` and extra Enters, and check liveness by writing a NUL
byte. Under read-only, AAA-authorized accounts those are rejected or
unwanted (D6, D16). This transport therefore uses netmiko only for the SSH
connection, authentication, channel I/O and the session log, and drives the
CLI itself.

The only data this transport ever writes to the device:

- the caller's command followed by `newline`, in `send_command()`
- `pager_response` each time the device shows its pager prompt
- one bare `newline` after login, only if the device did not print a prompt
  by itself within `prompt_wait` seconds
- `exit_command` followed by `newline`, in `close()`

Vendor quirks are class attributes; device packages subclass and override
them.
"""

import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, Protocol

try:
    import paramiko
    from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException
    from netmiko.base_connection import BaseConnection
except ImportError as exc:  # pragma: no cover
    raise ImportError("SSHTransport needs netmiko; install netsdk-core[ssh]") from exc

from netsdk.core.credentials import Credentials
from netsdk.core.exceptions import (
    AuthenticationError,
    CommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    HostKeyError,
)

log = logging.getLogger(__name__)

DEFAULT_KNOWN_HOSTS = Path.home() / ".netsdk" / "known_hosts"

# Serializes known_hosts read-modify-write across threads in this process.
_known_hosts_lock = threading.Lock()


class _QuietConnection(BaseConnection):
    """netmiko connection that never writes to the device on its own (D16)."""

    def _try_session_preparation(self, force_data: bool = True) -> None:
        # netmiko would send an Enter here, then `terminal width`,
        # `terminal length 0` and more Enters to find the prompt.
        return None

    def session_preparation(self) -> None:
        return None

    def disable_paging(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def set_terminal_width(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def cleanup(self, command: str = "") -> None:
        # SSHTransport.close() sends exit_command itself.
        return None

    def is_alive(self) -> bool:
        # netmiko's version writes a NUL byte to the channel.
        chan = self.remote_conn
        return bool(
            isinstance(chan, paramiko.Channel)
            and chan.transport is not None
            and chan.transport.is_active()
        )

    def _build_ssh_client(self) -> paramiko.SSHClient:
        client = self._get_ssh_client_instance()
        if self.alt_host_keys and Path(self.alt_key_file).is_file():
            # Load into memory only. SSHClient.load_host_keys() would make
            # paramiko rewrite the whole file on every new key, which races
            # between threads; SSHTransport saves new keys itself.
            client.get_host_keys().load(self.alt_key_file)
        client.set_missing_host_key_policy(self.key_policy)
        return client


class _Connection(Protocol):
    """The subset of a netmiko connection SSHTransport relies on."""

    remote_conn: Any

    def write_channel(self, out_data: str) -> None: ...

    def read_channel(self) -> str: ...

    def disconnect(self) -> None: ...


def render_terminal(text: str) -> str:
    """Apply carriage returns and backspaces the way a terminal would.

    Pagers erase their prompt with backspaces or `\\r` plus spaces; this
    turns raw channel data into what a person would have seen. Line endings
    are normalized to `\\n`.
    """
    text = re.sub(r"\r+\n", "\n", text)
    lines = []
    for line in text.split("\n"):
        if "\r" not in line and "\b" not in line:
            lines.append(line)
            continue
        screen: list[str] = []
        cursor = 0
        for char in line:
            if char == "\r":
                cursor = 0
            elif char == "\b":
                cursor = max(cursor - 1, 0)
            else:
                if cursor < len(screen):
                    screen[cursor] = char
                else:
                    screen.append(char)
                cursor += 1
        lines.append("".join(screen).rstrip())
    return "\n".join(lines)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _is_host_key_failure(exc: BaseException) -> bool:
    # netmiko re-raises paramiko's host key errors as NetmikoTimeoutException,
    # so look through the whole chain.
    return any(
        isinstance(e, paramiko.BadHostKeyException) or "known_hosts" in str(e)
        for e in _exception_chain(exc)
    )


def _root_message(exc: BaseException) -> str:
    message = str(_exception_chain(exc)[-1]).strip()
    return message.splitlines()[0] if message else type(exc).__name__


class SSHTransport:
    """Interactive CLI session over SSH with self-managed paging."""

    #: Characters that end a CLI prompt, e.g. `R1>` / `R1#`.
    prompt_terminators: ClassVar[str] = ">#"
    #: Regex for the pager prompt (matched at the end of the received data).
    pager_pattern: ClassVar[str | None] = None
    #: Keystroke that shows the next pager page.
    pager_response: ClassVar[str] = " "
    #: Regexes (multiline) that mark a rejected command.
    error_patterns: ClassVar[tuple[str, ...]] = ()
    #: How many non-blank output lines are scanned for error_patterns.
    error_scan_lines: ClassVar[int] = 3
    #: Sent on close(); None closes the channel without logging out.
    exit_command: ClassVar[str | None] = "exit"
    newline: ClassVar[str] = "\n"
    #: Seconds to wait for an unprompted prompt after login before sending Enter.
    prompt_wait: ClassVar[float] = 2.0
    #: Seconds to wait for the device to close the channel after exit_command.
    exit_wait: ClassVar[float] = 2.0
    poll_interval: ClassVar[float] = 0.05

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        *,
        session_log: Path,
        port: int = 22,
        timeout: float = 30.0,
        known_hosts_file: Path | None = DEFAULT_KNOWN_HOSTS,
        strict_host_keys: bool = False,
    ) -> None:
        self.host = host
        self.credentials = credentials
        self.session_log = session_log
        self.port = port
        self.timeout = timeout
        self.known_hosts_file = known_hosts_file
        self.strict_host_keys = strict_host_keys
        self.base_prompt: str | None = None

        self._conn: _Connection | None = None
        self._log_file: io.BufferedWriter | None = None
        self._prompt_re: re.Pattern[str] | None = None
        self._in_sync = False

        terms = re.escape(self.prompt_terminators)
        self._terms = terms
        # Hostnames never contain "<", so banner lines like "<WARNING>" can't
        # be mistaken for a prompt.
        self._any_prompt_re = re.compile(rf"(?:^|\n)(?P<base>[^\s<{terms}]+)[{terms}]\s*$")
        self._pager_re = (
            re.compile(rf"(?:{self.pager_pattern})\s*$") if self.pager_pattern else None
        )
        self._error_res = [re.compile(p, re.MULTILINE) for p in self.error_patterns]

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def open(self) -> None:
        if self._conn is not None:
            return
        self.session_log.parent.mkdir(parents=True, exist_ok=True)
        # Binary mode keeps the log byte-exact (no newline translation on
        # Windows), so a log can be used directly as a raw fixture.
        log_file = self.session_log.open("wb")
        try:
            self._conn = self._connect(log_file)
        except BaseException:
            log_file.close()
            raise
        self._log_file = log_file
        try:
            self._detect_prompt()
        except BaseException:
            self._teardown()
            raise
        self._in_sync = True
        log.info("%s: session open, prompt %r", self.host, self.base_prompt)

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            if self.exit_command is not None and not self._channel_closed():
                self._write(self.exit_command + self.newline)
                self._drain(self.exit_wait)
        except Exception as exc:
            log.debug("%s: ignoring error while logging out: %s", self.host, exc)
        finally:
            self._teardown()
        log.info("%s: session closed", self.host)

    def send_command(self, command: str, *, timeout: float | None = None) -> str:
        """Run one CLI command and return its output.

        The command echo and trailing prompt are stripped, pager prompts are
        answered and erased, and line endings are `\\n`.
        """
        if not command.strip() or "\n" in command or "\r" in command:
            raise ValueError("send_command() takes exactly one single-line command")
        if self._conn is None or self._prompt_re is None:
            raise DeviceConnectionError("transport is not open", host=self.host)
        if not self._in_sync:
            raise DeviceConnectionError(
                "session is out of sync after an earlier timeout; reopen it", host=self.host
            )

        self._write(command + self.newline)
        raw, match, pages = self._read_until(
            self._prompt_re, self.timeout if timeout is None else timeout, pager=True
        )
        if match is None:
            # Output may still be arriving; the next command would read it.
            self._in_sync = False
            raise DeviceTimeoutError(f"timed out waiting for output of {command!r}", host=self.host)

        output = self._clean(raw, command, pages)
        self._raise_for_errors(command, output)
        return output

    # -- connection -------------------------------------------------------

    def _connect(self, log_file: io.BufferedWriter) -> _Connection:
        creds = self.credentials
        if creds.key_file is not None and not creds.key_file.is_file():
            raise AuthenticationError(f"SSH key file not found: {creds.key_file}", host=self.host)
        known_hosts = self._prepare_known_hosts()
        try:
            conn = _QuietConnection(
                host=self.host,
                port=self.port,
                username=creds.username,
                password=creds.password.get_secret_value() if creds.password else None,
                key_file=str(creds.key_file) if creds.key_file else None,
                passphrase=(
                    creds.key_passphrase.get_secret_value() if creds.key_passphrase else None
                ),
                # Use only the credentials given: no ~/.ssh key search, no agent.
                use_keys=False,
                allow_agent=False,
                ssh_strict=self.strict_host_keys,
                system_host_keys=False,
                alt_host_keys=known_hosts is not None,
                alt_key_file=str(known_hosts) if known_hosts else "",
                conn_timeout=self.timeout,
                auth_timeout=self.timeout,
                banner_timeout=self.timeout,
                session_log=log_file,
                # Keep raw \r and backspaces: the pager handling needs them
                # and the session log should be an exact capture.
                disable_lf_normalization=True,
                device_type="netsdk",
            )
        except NetmikoAuthenticationException as exc:
            raise AuthenticationError(
                f"authentication failed: {_root_message(exc)}", host=self.host
            ) from exc
        except (NetmikoTimeoutException, paramiko.SSHException, OSError, EOFError) as exc:
            if _is_host_key_failure(exc):
                raise HostKeyError(
                    f"SSH host key rejected: {_root_message(exc)}", host=self.host
                ) from exc
            raise DeviceConnectionError(
                f"could not connect to port {self.port}: {_root_message(exc)}", host=self.host
            ) from exc

        if known_hosts is not None:
            self._remember_host_key(conn, known_hosts)
        return conn

    def _prepare_known_hosts(self) -> Path | None:
        if self.known_hosts_file is None:
            return None
        self.known_hosts_file.parent.mkdir(parents=True, exist_ok=True)
        self.known_hosts_file.touch(exist_ok=True)
        return self.known_hosts_file

    def _remember_host_key(self, conn: _QuietConnection, path: Path) -> None:
        client = conn.remote_conn_pre
        ssh_transport = client.get_transport() if client is not None else None
        if ssh_transport is None:
            return
        key = ssh_transport.get_remote_server_key()
        entry = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        with _known_hosts_lock:
            keys = paramiko.HostKeys(str(path))
            if keys.check(entry, key):
                return
            keys.add(entry, key.get_name(), key)
            keys.save(str(path))
        log.warning(
            "%s: saved new SSH host key %s %s to %s",
            self.host,
            key.get_name(),
            key.fingerprint,
            path,
        )

    def _detect_prompt(self) -> None:
        _, match, _ = self._read_until(self._any_prompt_re, self.prompt_wait, pager=False)
        if match is None:
            self._write(self.newline)
            _, match, _ = self._read_until(self._any_prompt_re, self.timeout, pager=False)
        if match is None:
            raise DeviceTimeoutError("no CLI prompt received after login", host=self.host)
        self.base_prompt = match["base"]
        self._prompt_re = re.compile(rf"(?:^|\n){re.escape(self.base_prompt)}[{self._terms}]\s*$")

    def _teardown(self) -> None:
        conn, log_file = self._conn, self._log_file
        self._conn = self._log_file = self._prompt_re = None
        self.base_prompt = None
        self._in_sync = False
        try:
            if conn is not None:
                # Flushes netmiko's session log buffer into our file, closes SSH.
                conn.disconnect()
        except Exception as exc:
            log.debug("%s: ignoring error during disconnect: %s", self.host, exc)
        finally:
            if log_file is not None:
                log_file.close()

    # -- channel I/O ------------------------------------------------------

    def _write(self, data: str) -> None:
        # Every byte sent to the device goes through here (D16).
        assert self._conn is not None
        log.debug("%s: send %r", self.host, data)
        try:
            self._conn.write_channel(data)
        except (OSError, EOFError, paramiko.SSHException) as exc:
            raise DeviceConnectionError(f"write failed: {exc}", host=self.host) from exc

    def _read(self) -> str:
        assert self._conn is not None
        try:
            return self._conn.read_channel()
        except (OSError, EOFError, paramiko.SSHException) as exc:
            raise DeviceConnectionError(f"read failed: {exc}", host=self.host) from exc

    def _channel_closed(self) -> bool:
        chan = getattr(self._conn, "remote_conn", None)
        return chan is None or bool(getattr(chan, "closed", False))

    def _read_until(
        self, pattern: re.Pattern[str], timeout: float, *, pager: bool
    ) -> tuple[str, re.Match[str] | None, int]:
        """Read until `pattern` matches the last line, answering pager prompts.

        Returns the raw data, the match (None on timeout) and how many pager
        prompts were answered.
        """
        deadline = time.monotonic() + timeout
        raw = ""
        pages = 0
        pager_from = 0
        while time.monotonic() < deadline:
            chunk = self._read()
            if not chunk:
                if self._channel_closed():
                    raise DeviceConnectionError("device closed the session", host=self.host)
                time.sleep(self.poll_interval)
                continue
            raw += chunk
            # Prompts sit on the last line, so only look there: rescanning
            # the whole buffer would be quadratic on large outputs.
            last_line = raw.rfind("\n") + 1
            if (
                pager
                and self._pager_re is not None
                and self._pager_re.search(raw, max(pager_from, last_line))
            ):
                self._write(self.pager_response)
                pages += 1
                pager_from = len(raw)
                continue
            tail = render_terminal(raw[max(last_line - 1, 0) :])
            match = pattern.search(tail)
            if match is not None:
                return raw, match, pages
        return raw, None, pages

    def _drain(self, timeout: float) -> None:
        # Keep reading after exit so the session log captures the logout.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._channel_closed():
            if not self._read():
                time.sleep(self.poll_interval)

    # -- output -----------------------------------------------------------

    def _clean(self, raw: str, command: str, pages: int) -> str:
        text = render_terminal(raw)
        if pages and self.pager_pattern:
            # Devices that don't erase their pager prompt leave it in place.
            text = re.sub(self.pager_pattern, "", text, count=pages)
        lines = text.split("\n")
        if lines and lines[0].strip() == command.strip():
            lines = lines[1:]
        assert self._prompt_re is not None
        if lines and self._prompt_re.search("\n" + lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines).strip("\n")

    def _raise_for_errors(self, command: str, output: str) -> None:
        head = "\n".join(
            [line for line in output.split("\n") if line.strip()][: self.error_scan_lines]
        )
        for error_re in self._error_res:
            found = error_re.search(head)
            if found is not None:
                raise CommandError(
                    f"device rejected {command!r}: {found.group(0).strip()}",
                    command=command,
                    output=output,
                    host=self.host,
                )


__all__ = ["DEFAULT_KNOWN_HOSTS", "SSHTransport", "render_terminal"]
