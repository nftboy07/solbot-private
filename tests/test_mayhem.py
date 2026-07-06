"""Tests for Mayhem Mode detection."""

from solbot.mayhem import metadata_indicates_mayhem, ws_payload_indicates_mayhem


def test_metadata_mayhem_mode_flag():
    assert metadata_indicates_mayhem({"mayhem_mode": True}) is True


def test_metadata_is_mayhem_flag():
    assert metadata_indicates_mayhem({"is_mayhem": True}) is True


def test_metadata_mayhem_dict_active():
    assert metadata_indicates_mayhem({"mayhem": {"active": True}}) is True


def test_metadata_mayhem_state_active():
    assert metadata_indicates_mayhem({"mayhem_state": "active"}) is True


def test_metadata_inactive_states_not_mayhem():
    for state in (0, "0", "inactive", "none", False):
        assert metadata_indicates_mayhem({"mayhem_state": state}) is False


def test_metadata_empty_or_none():
    assert metadata_indicates_mayhem(None) is False
    assert metadata_indicates_mayhem({}) is False


def test_ws_payload_delegates_to_metadata():
    payload = {"mayhemMode": True, "symbol": "TEST"}
    assert ws_payload_indicates_mayhem(payload) is True