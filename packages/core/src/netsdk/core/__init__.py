"""Core building blocks shared by netsdk device packages."""

from netsdk.core.exceptions import (
    AuthenticationError,
    CommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    NetSDKError,
    NotSupportedError,
    ParseError,
)

__version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "CommandError",
    "DeviceConnectionError",
    "DeviceTimeoutError",
    "NetSDKError",
    "NotSupportedError",
    "ParseError",
]
