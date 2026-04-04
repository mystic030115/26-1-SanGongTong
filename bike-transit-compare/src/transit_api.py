"""Fetch or resolve transit times between points (implement with your API)."""

from __future__ import annotations

import os
from typing import Any


def fetch_transit_leg(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> dict[str, Any]:
    """
    Return transit summary for one origin–destination pair.
    Replace with real API calls using os.environ / config.
    """
    _ = (origin_lat, origin_lon, dest_lat, dest_lon)
    _key = os.environ.get("TRANSIT_API_KEY", "")
    if not _key:
        return {
            "duration_sec": None,
            "transfers": None,
            "route_summary": "configure TRANSIT_API_KEY in .env",
        }
    return {"duration_sec": None, "transfers": None, "route_summary": ""}
