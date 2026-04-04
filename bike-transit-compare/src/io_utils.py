"""Load raw inputs and write outputs."""

from __future__ import annotations

import pandas as pd

from . import config


def load_trips() -> pd.DataFrame:
    return pd.read_csv(config.TRIPS_CSV)


def load_stations() -> pd.DataFrame:
    return pd.read_excel(config.STATIONS_XLSX)


def load_transit_pairs() -> pd.DataFrame:
    return pd.read_csv(config.TRANSIT_PAIRS_CSV)


def save_trips_with_transit(df: pd.DataFrame) -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(config.TRIPS_WITH_TRANSIT_XLSX, index=False)
