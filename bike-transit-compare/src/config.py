"""Paths and environment-driven settings."""

from pathlib import Path

import dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = DATA_DIR / "output"

TRIPS_CSV = RAW_DIR / "trips.csv"
STATIONS_XLSX = RAW_DIR / "stations.xlsx"
TRANSIT_PAIRS_CSV = CACHE_DIR / "transit_pairs.csv"
TRIPS_WITH_TRANSIT_XLSX = OUTPUT_DIR / "trips_with_transit.xlsx"

dotenv.load_dotenv(_PROJECT_ROOT / ".env")
