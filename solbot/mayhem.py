"""Pump.fun Mayhem Mode detection — unsellable tokens, never buy."""

from __future__ import annotations

from typing import Any, Dict, Optional

TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

_INACTIVE_MAYHEM_STATES = frozenset({0, "0", "inactive", "none", "ended", "closed", False})


def metadata_indicates_mayhem(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    if meta.get("mayhem_mode") is True:
        return True
    if meta.get("is_mayhem") is True:
        return True
    if meta.get("isMayhem") is True:
        return True
    if meta.get("mayhemMode") is True:
        return True
    mayhem = meta.get("mayhem")
    if mayhem is not None and mayhem is not False:
        if isinstance(mayhem, dict):
            return bool(mayhem.get("active") or mayhem.get("enabled"))
        return True
    state = meta.get("mayhem_state")
    if state is not None and state not in _INACTIVE_MAYHEM_STATES:
        return True
    if meta.get("mayhemState") is not None and meta.get("mayhemState") not in _INACTIVE_MAYHEM_STATES:
        return True
    return False


def ws_payload_indicates_mayhem(data: Optional[Dict[str, Any]]) -> bool:
    return metadata_indicates_mayhem(data)