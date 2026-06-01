"""Supervisor Redis store helpers."""

from backend.supervisor.store import canonical_name


def test_canonical_vwap_meanrev_1h_distinct_from_vwap_meanrev():
    assert canonical_name("vwap_meanrev_1h") == "vwap_meanrev_1h"
    assert canonical_name("vwap_meanrev") == "vwap_meanrev"
    assert canonical_name("vwap_meanreversion") == "vwap_meanrev"
