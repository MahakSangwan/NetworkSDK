"""SSH transport settings for Cisco NX-OS."""

from typing import ClassVar

from netsdk.core.transports.ssh import SSHTransport


class NXOSSSHTransport(SSHTransport):
    """NX-OS CLI over SSH, paging through `--More--` (no `terminal length 0`, D25)."""

    # Some terminals get the pager prompt in reverse video; render_terminal()
    # drops the escapes from the output, the pattern has to allow for them.
    pager_pattern = r"(?:\x1b\[[0-9;]*m)* ?--More-- ?(?:\x1b\[[0-9;]*m)*"
    error_patterns: ClassVar[tuple[str, ...]] = (
        r"^% Invalid command",
        r"^% Invalid parameter",
        r"^% Invalid number",
        r"^% Incomplete command",
        r"^% Ambiguous command",
        r"^% Permission denied",
        r"^% Authorization failed",
        r"^% ?[Cc]ommand authorization failed",
        r"^[Cc]ommand authorization failed",
    )
    # NX-OS 9.3 prints up to four lines of its own (`SB: Permission denied.`,
    # `drbg_instantiate failed`) before any `| json` output, error or not,
    # and a rejected command starts with a `^` marker line.
    error_scan_lines = 8
