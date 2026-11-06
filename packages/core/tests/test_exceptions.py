import pytest

from netsdk.core import (
    AuthenticationError,
    CommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    NetSDKError,
    NotSupportedError,
    ParseError,
)


@pytest.mark.parametrize(
    "exc_type",
    [
        DeviceConnectionError,
        AuthenticationError,
        DeviceTimeoutError,
        CommandError,
        ParseError,
        NotSupportedError,
    ],
)
def test_all_errors_derive_from_base(exc_type: type[NetSDKError]) -> None:
    assert issubclass(exc_type, NetSDKError)


def test_authentication_error_is_a_connection_error() -> None:
    assert issubclass(AuthenticationError, DeviceConnectionError)


def test_builtins_are_not_shadowed() -> None:
    assert not issubclass(DeviceConnectionError, ConnectionError)
    assert not issubclass(DeviceTimeoutError, TimeoutError)


def test_str_includes_host_when_known() -> None:
    assert str(NetSDKError("boom", host="sw1")) == "[sw1] boom"
    assert str(NetSDKError("boom")) == "boom"


def test_command_error_keeps_command_and_output() -> None:
    err = CommandError("rejected", command="show foo", output="% Invalid input", host="r1")
    assert err.command == "show foo"
    assert err.output == "% Invalid input"
    assert err.host == "r1"
