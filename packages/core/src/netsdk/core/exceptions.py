"""Exception hierarchy shared by all netsdk packages.

Everything raised by netsdk derives from `NetSDKError`, so callers that run
getters across many devices can catch one type per device. Names avoid
shadowing builtins such as `ConnectionError` and `TimeoutError`.

    NetSDKError
    ├── DeviceConnectionError
    │   └── AuthenticationError
    ├── DeviceTimeoutError
    ├── CommandError
    ├── ParseError
    └── NotSupportedError
"""


class NetSDKError(Exception):
    """Base class for every error raised by netsdk.

    `host` identifies the device involved, when known, so errors collected
    from a batch of devices remain attributable.
    """

    def __init__(self, message: str, *, host: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.host = host

    def __str__(self) -> str:
        if self.host:
            return f"[{self.host}] {self.message}"
        return self.message


class DeviceConnectionError(NetSDKError):
    """The device could not be reached or the session dropped."""


class AuthenticationError(DeviceConnectionError):
    """The device rejected the supplied credentials."""


class DeviceTimeoutError(NetSDKError):
    """An operation did not complete within its timeout."""


class CommandError(NetSDKError):
    """The device rejected a command or returned an error for it."""

    def __init__(
        self,
        message: str,
        *,
        command: str,
        output: str | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__(message, host=host)
        self.command = command
        self.output = output


class ParseError(NetSDKError):
    """Raw device output could not be turned into a model."""


class NotSupportedError(NetSDKError):
    """The device, OS version or platform does not support the operation."""
