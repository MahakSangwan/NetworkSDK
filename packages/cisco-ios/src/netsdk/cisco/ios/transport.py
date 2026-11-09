"""SSH transport settings for Cisco IOS."""

from typing import ClassVar

from netsdk.core.transports.ssh import SSHTransport


class IOSSSHTransport(SSHTransport):
    """IOS CLI over SSH, paging through `--More--` (no `terminal length 0`, D6)."""

    pager_pattern = r" ?--More-- ?"
    error_patterns: ClassVar[tuple[str, ...]] = (
        r"^% Invalid input detected",
        r"^% Incomplete command",
        r"^% Ambiguous command",
        r"^% Unknown command",
        r"^% Unrecognized command",
        r"^% Authorization failed",
        r"^% ?[Cc]ommand authorization failed",
        r"^[Cc]ommand authorization failed",
    )
