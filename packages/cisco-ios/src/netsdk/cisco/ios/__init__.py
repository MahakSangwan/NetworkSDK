"""netsdk SDK for Cisco IOS (classic)."""

from netsdk.cisco.ios.device import CiscoIOS
from netsdk.cisco.ios.transport import IOSSSHTransport

__version__ = "0.1.0"

__all__ = ["CiscoIOS", "IOSSSHTransport"]
