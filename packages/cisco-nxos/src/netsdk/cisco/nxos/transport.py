"""SSH transport settings for Cisco NX-OS."""

from typing import ClassVar

from netsdk.core.transports.ssh import SSHTransport

#: SSH terminal width for NX-OS sessions (D42). On a terminal wider than 255
#: columns, 7.3(11)N1(1) cuts output lines at 255 characters and drops the
#: next one; at 255 or less it wraps at the terminal width and loses nothing
#: (live, 2026-11-18: widths 80-255 clean, 256 and 511 damaged).
NXOS_TERMINAL_WIDTH = 255


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
