"""Models shared by all vendors.

Only truly cross-vendor concepts live here; device packages subclass them to
add what their platform reports. Models are frozen (D8): a getter's result is
a snapshot, not something to edit.
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from netsdk.core.formats import Duplex, MacAddress


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


class Interface(NetSDKModel):
    """One interface as the device reports it. Values use netsdk.core.formats."""

    #: Full name as the device prints it, never abbreviated: "GigabitEthernet1/0/1".
    name: str = Field(min_length=1)
    description: str | None = None
    #: Administratively enabled (not shut down).
    enabled: bool
    #: Operationally up (line protocol up).
    oper_up: bool
    mac_address: MacAddress | None = None
    mtu: int | None = Field(default=None, gt=0)
    #: Speed the device reports, in kbit/s: negotiated, or configured on a port
    #: that is down. None when it only says auto.
    speed: int | None = Field(default=None, gt=0)
    duplex: Duplex | None = None
