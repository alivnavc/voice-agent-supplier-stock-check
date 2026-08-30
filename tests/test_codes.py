"""HCPCS code normalisation / readback checks (FR-8, FR-16)."""
import pytest

from supplier_caller.codes import normalize_hcpcs, readback_matches, spell_code


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("K0001", "K0001"),
        ("k0001", "K0001"),
        ("K 0001", "K0001"),
        ("K-0-0-0-1", "K0001"),
        ("K zero zero zero one", "K0001"),
        ("kay zero zero zero three", "K0003"),
        ("K oh oh oh one", "K0001"),
        ("So that's K, 0, 0, 0, 3?", "K0003"),
        ("E1130 right?", "E1130"),
        ("k triple zero one", "K0001"),
    ],
)
def test_normalize_hcpcs(spoken: str, expected: str) -> None:
    assert normalize_hcpcs(spoken) == expected


@pytest.mark.parametrize("spoken", ["hello there", "we take Medicare", "60640", ""])
def test_normalize_returns_none_when_no_code(spoken: str) -> None:
    assert normalize_hcpcs(spoken) is None


def test_spell_code() -> None:
    assert spell_code("K0001") == "K zero zero zero one"
    assert spell_code("E1130") == "E one one three zero"


def test_readback_matches() -> None:
    assert readback_matches("K0001", "K zero zero zero one") is True
    assert readback_matches("K0001", "K0003") is False
    assert readback_matches("K0001", "no code here") is None
