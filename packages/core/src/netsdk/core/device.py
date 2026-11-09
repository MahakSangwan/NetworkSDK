"""Base class for every netsdk device."""

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Self

from netsdk.core.credentials import Credentials

DEFAULT_SESSION_LOG_DIR = Path("session_logs")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class Device(ABC):
    """Lifecycle shared by all devices: open, close and context manager.

    Subclasses implement `open()`/`close()` by composing a transport (from
    core or their own). Getters live in the subclasses, never here.

    Every session is recorded to a log file under `session_log_dir`; this
    cannot be switched off (D17).
    """

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        *,
        port: int | None = None,
        timeout: float = 30.0,
        session_log_dir: str | PathLike[str] = DEFAULT_SESSION_LOG_DIR,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.host = host
        self.credentials = credentials
        self.port = port
        self.timeout = timeout
        self.session_log_dir = Path(session_log_dir)

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether a session to the device is currently established."""

    @abstractmethod
    def open(self) -> None:
        """Connect and authenticate. Calling it on an open device is a no-op."""

    @abstractmethod
    def close(self) -> None:
        """End the session. Calling it on a closed device is a no-op."""

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def new_session_log_path(self) -> Path:
        """Return a fresh, unused log file path for a new session.

        Named `<host>_<UTC timestamp>.log`, with a numeric suffix if a session
        to the same host already started in the same second.
        """
        self.session_log_dir.mkdir(parents=True, exist_ok=True)
        stem = (
            f"{_UNSAFE_FILENAME_CHARS.sub('_', self.host)}_"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        path = self.session_log_dir / f"{stem}.log"
        counter = 1
        while path.exists():
            path = self.session_log_dir / f"{stem}-{counter}.log"
            counter += 1
        return path

    def __repr__(self) -> str:
        return f"{type(self).__name__}(host={self.host!r})"
