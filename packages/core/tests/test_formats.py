import pytest
from pydantic import ValidationError

from netsdk.core import Interface
from netsdk.core.formats import normalize_mac


@pytest.mark.parametrize(
    "value",
    [
        "aabb.ccdd.eeff",
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aabbccddeeff",
        " AABB.CCDD.EEFF ",
    ],
)
def test_mac_notations_normalize_to_one_format(value: str) -> None:
    assert normalize_mac(value) == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("value", ["", "aabb.ccdd", "gggg.hhhh.iiii", "aabb.ccdd.eeff.0011"])
def test_invalid_mac_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="not a MAC address"):
        normalize_mac(value)


def test_models_store_the_canonical_mac() -> None:
    interface = Interface(name="Gi1/0/1", enabled=True, oper_up=True, mac_address="0014.1C57.A4C0")
    assert interface.mac_address == "00:14:1c:57:a4:c0"
    with pytest.raises(ValidationError):
        Interface(name="Gi1/0/1", enabled=True, oper_up=True, mac_address="nonsense")


def test_interface_rejects_unknown_duplex_and_non_positive_numbers() -> None:
    with pytest.raises(ValidationError):
        Interface(name="x", enabled=True, oper_up=True, duplex="Full-duplex")
    with pytest.raises(ValidationError):
        Interface(name="x", enabled=True, oper_up=True, speed=0)
