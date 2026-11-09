"""Login credentials shared by all transports."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Credentials(BaseModel):
    """Username plus a password, an SSH private key, or both.

    Secrets are `SecretStr`, so they never show up in reprs or logs. Use
    `.get_secret_value()` only at the point of handing them to a library.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str = Field(min_length=1)
    password: SecretStr | None = None
    key_file: Path | None = None
    key_passphrase: SecretStr | None = None

    @model_validator(mode="after")
    def _check_auth_method(self) -> Self:
        if self.password is None and self.key_file is None:
            raise ValueError("credentials need a password, a key_file, or both")
        if self.key_passphrase is not None and self.key_file is None:
            raise ValueError("key_passphrase is only valid together with key_file")
        return self
