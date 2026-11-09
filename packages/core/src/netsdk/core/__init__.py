"""Core building blocks shared by netsdk device packages."""

from netsdk.core.credentials import Credentials
from netsdk.core.device import Device
from netsdk.core.exceptions import (
    AuthenticationError,
    CommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    HostKeyError,
    NetSDKError,
    NotSupportedError,
    ParseError,
)
from netsdk.core.transports import Transport

__version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "CommandError",
    "Credentials",
    "Device",
    "DeviceConnectionError",
    "DeviceTimeoutError",
    "HostKeyError",
    "NetSDKError",
    "NotSupportedError",
    "ParseError",
    "Transport",
]
