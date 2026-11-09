"""Models shared by all vendors.

Only truly cross-vendor concepts live here; device packages subclass them to
add what their platform reports. Models are frozen (D8): a getter's result is
a snapshot, not something to edit.
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class NetSDKModel(BaseModel):
    """Base for every netsdk model: immutable, and unknown fields are errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Facts(NetSDKModel):
    """Identity of one device (one chassis; stacks are not modelled, D26)."""

    hostname: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    model: str = Field(min_length=1)
    serial_number: str = Field(min_length=1)
    #: Exactly as the device prints it, e.g. "15.2(7)E4".
    os_version: str = Field(min_length=1)
    #: As the device reports it (minute precision on most platforms), not
    #: derived from clocks. None when the device doesn't say.
    uptime: timedelta | None = None
