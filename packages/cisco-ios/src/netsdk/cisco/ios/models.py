"""IOS-specific models."""

from netsdk.core import Facts


class IOSFacts(Facts):
    """Facts plus what IOS `show version` adds. Extras are None when absent."""

    #: Image family in parentheses on the version line, e.g. "C2960X-UNIVERSALK9-M".
    software_image: str | None = None
    #: Booted image path without the filesystem prefix, e.g. "/c2960x-....bin".
    running_image: str | None = None
    config_register: str | None = None
    reload_reason: str | None = None
