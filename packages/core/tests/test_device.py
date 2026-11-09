from pathlib import Path

import pytest
from pydantic import ValidationError

from netsdk.core import Credentials, Device


class DummyDevice(Device):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.calls: list[str] = []
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self.calls.append("open")
        self._open = True

    def close(self) -> None:
        self.calls.append("close")
        self._open = False


CREDS = Credentials(username="ro", password="pw")


def test_context_manager_opens_and_closes() -> None:
    device = DummyDevice("r1", CREDS)
    with device as opened:
        assert opened is device
        assert device.is_open
    assert device.calls == ["open", "close"]


def test_context_manager_closes_on_error() -> None:
    device = DummyDevice("r1", CREDS)
    with pytest.raises(RuntimeError), device:
        raise RuntimeError("boom")
    assert device.calls == ["open", "close"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"host": ""}, "host"), ({"host": "r1", "timeout": 0}, "timeout")],
)
def test_rejects_bad_arguments(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DummyDevice(credentials=CREDS, **kwargs)


def test_session_log_paths_are_unique_and_filename_safe(tmp_path: Path) -> None:
    device = DummyDevice("fe80::1%eth0", CREDS, session_log_dir=tmp_path / "logs")
    first = device.new_session_log_path()
    first.touch()
    second = device.new_session_log_path()
    assert first != second
    assert first.parent == tmp_path / "logs"
    assert first.name.startswith("fe80__1_eth0_")
    assert first.suffix == ".log"


def test_repr_has_no_secrets() -> None:
    assert "pw" not in repr(DummyDevice("r1", CREDS))


# -- Credentials ---------------------------------------------------------------


def test_credentials_accept_password_key_or_both(tmp_path: Path) -> None:
    Credentials(username="ro", password="pw")
    Credentials(username="ro", key_file=tmp_path / "id_ed25519")
    Credentials(username="ro", password="pw", key_file=tmp_path / "id_ed25519")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"username": "ro"},
        {"username": "", "password": "pw"},
        {"username": "ro", "password": "pw", "key_passphrase": "x"},
        {"username": "ro", "password": "pw", "domain": "x"},
    ],
    ids=["no-secret", "empty-username", "passphrase-without-key", "unknown-field"],
)
def test_credentials_reject_invalid(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Credentials(**kwargs)


def test_credentials_hide_secrets() -> None:
    creds = Credentials(username="ro", password="hunter2")
    assert "hunter2" not in repr(creds)
    assert "hunter2" not in str(creds)
