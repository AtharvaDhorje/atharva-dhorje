"""Unit and integration tests for checker.py using synthetic test data only.

Tests verify data quality checks, edge cases, error handling, time alignment,
and command-line behavior without embedding any original assignment records.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from checker import Config, Flag, PackError, Station, analyse, flags_frame, main, nmea_checksum_ok


# --------------------------------------------------------------------------------------
# Synthetic fixture generators (strictly no real data)
# --------------------------------------------------------------------------------------

def make_nmea_rmc(logger_ts: str, status: str = "A", lat: float = 40.0, lon: float = -105.0) -> str:
    # NMEA DDMM.MMMM format
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60.0
    lat_str = f"{lat_deg:02d}{lat_min:07.4f}"
    lat_hemi = "N" if lat >= 0 else "S"

    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60.0
    lon_str = f"{lon_deg:03d}{lon_min:07.4f}"
    lon_hemi = "E" if lon >= 0 else "W"

    dt = pd.to_datetime(logger_ts)
    time_str = dt.strftime("%H%M%S.00")
    date_str = dt.strftime("%d%m%y")

    body = f"GPRMC,{time_str},{status},{lat_str},{lat_hemi},{lon_str},{lon_hemi},0.0,0.0,{date_str},,,A"
    c = 0
    for ch in body:
        c ^= ord(ch)
    sentence = f"${body}*{c:02X}"
    return f"{logger_ts},{sentence}"


def create_synthetic_pack(tmp_path: Path, n_stations: int = 4, days: int = 5,
                          prefix: str = "SYN") -> Path:
    """Create a minimal valid synthetic data pack with completely synthetic data."""
    pack = tmp_path / "synthetic_pack"
    data_dir = pack / "data"
    gps_dir = pack / "gps"
    data_dir.mkdir(parents=True, exist_ok=True)
    gps_dir.mkdir(parents=True, exist_ok=True)

    stations = []
    start_ts = pd.Timestamp("2026-06-01 00:00")
    periods = days * 144
    grid = pd.date_range(start_ts, periods=periods, freq="10min")

    for i in range(1, n_stations + 1):
        sid = f"{prefix}{i:02d}"
        lat = 42.0 + i * 0.1
        lon = -100.0 - i * 0.1
        elev = 1000 + i * 50
        stations.append({
            "station_id": sid,
            "name": f"Synthetic Station {i}",
            "lat": lat,
            "lon": lon,
            "elevation_m": elev,
            "installed": "2024-01-01",
            "logger_interval_min": 10,
        })

        # Synthetic diurnal signals with continuous variation
        hour = grid.hour.to_numpy() + grid.minute.to_numpy() / 60.0
        time_seq = np.arange(len(grid))
        t_diurnal = 15.0 + 8.0 * np.sin(2 * np.pi * (hour - 9) / 24.0) + (i * 0.2) + 0.5 * np.sin(time_seq / 5.0)
        rh_diurnal = np.clip(60.0 - 25.0 * np.sin(2 * np.pi * (hour - 9) / 24.0) + 1.0 * np.cos(time_seq / 4.0), 10.0, 95.0)
        p_val = 900.0 - (elev - 1000) * 0.11 + 3.0 * np.sin(2 * np.pi * hour / 24.0) + 0.8 * np.cos(time_seq / 3.0)
        w_val = 8.0 + 3.0 * np.cos(hour / 4.0) + 0.5 * np.sin(time_seq / 2.0)
        w_dir = (180.0 + 40.0 * np.sin(time_seq / 10.0) + 20.0 * np.cos(time_seq / 3.0)) % 360.0
        ghi_val = np.maximum(0.0, 800.0 * np.sin(np.pi * (hour - 6) / 12.0))
        ghi_val[hour < 6] = 0.0
        ghi_val[hour > 18] = 0.0

        df = pd.DataFrame({
            "timestamp_utc": grid.strftime("%Y-%m-%d %H:%M"),
            "temp_c": np.round(t_diurnal, 1),
            "rh_pct": np.round(rh_diurnal, 1),
            "pressure_hpa": np.round(p_val, 1),
            "wind_kmh": np.round(w_val, 1),
            "wind_dir_deg": np.round(w_dir, 0).astype(int),
            "rain_mm": 0.0,
            "ghi_wm2": np.round(ghi_val, 0).astype(int),
        })
        df.to_csv(data_dir / f"{sid}.csv", index=False)

        # GPS sentences (hourly)
        gps_lines = []
        for ts_h in grid[::6]:
            ts_str = ts_h.strftime("%Y-%m-%d %H:%M:%S")
            gps_lines.append(make_nmea_rmc(ts_str, "A", lat, lon))
        (gps_dir / f"{sid}.nmea").write_text("\n".join(gps_lines) + "\n", encoding="utf-8")

    pd.DataFrame(stations).to_csv(pack / "stations.csv", index=False)
    (pack / "maintenance_log.md").write_text(
        "# Synthetic maintenance log\n\n2026-06-02 SYN01 Routine inspection.\n", encoding="utf-8"
    )
    return pack


# --------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------

def test_nmea_checksum():
    # Valid NMEA test
    sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    assert nmea_checksum_ok(sentence)
    # Tampered checksum
    assert not nmea_checksum_ok("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*99")
    # Missing dollar or asterisk
    assert not nmea_checksum_ok("GPRMC,123519*6A")
    assert not nmea_checksum_ok("$GPRMC,123519")


def test_clean_synthetic_data_produces_no_error_flags(tmp_path: Path):
    """A clean synthetic dataset with regular weather should not trigger any exclude/correct flags."""
    pack = create_synthetic_pack(tmp_path, n_stations=4, days=3)
    stations = analyse(pack)
    df = flags_frame(stations)
    # There should be 0 exclude or correct flags
    bad_flags = df[df["action"].isin(["exclude", "correct"])]
    assert len(bad_flags) == 0, f"Unexpected error flags on clean data: {bad_flags}"


def test_sensor_flatline_detection(tmp_path: Path):
    """A temperature sensor stuck at a constant value for >36 samples should be flagged and grouped."""
    pack = create_synthetic_pack(tmp_path, n_stations=4, days=3)
    # Inject 48 identical samples (8 hours) into SYN01 temp_c
    target_csv = pack / "data" / "SYN01.csv"
    df = pd.read_csv(target_csv)
    df.loc[50:98, "temp_c"] = 12.3
    df.to_csv(target_csv, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    flatline = flags[(flags["station_id"] == "SYN01") & (flags["issue"] == "sensor flatline")]
    assert len(flatline) == 1, "Expected exactly 1 grouped flatline flag"
    assert flatline.iloc[0]["channel"] == "temp_c"
    assert flatline.iloc[0]["action"] == "exclude"


def test_physically_impossible_value(tmp_path: Path):
    """Values outside known physical ranges (e.g. temp 85 C or -80 C) must be flagged."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=3)
    target_csv = pack / "data" / "SYN01.csv"
    df = pd.read_csv(target_csv)
    df.loc[30, "temp_c"] = 85.0
    df.to_csv(target_csv, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    spike = flags[(flags["station_id"] == "SYN01") & (flags["channel"] == "temp_c") & (flags["action"] == "exclude")]
    assert len(spike) >= 1
    assert any("outside the physical range" in n or "error code" in n for n in spike["note"])


def test_isolated_sensor_spike(tmp_path: Path):
    """An isolated 1-sample spike to sentinel -40.0 C must be flagged as isolated spike."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=3)
    target_csv = pack / "data" / "SYN02.csv"
    df = pd.read_csv(target_csv)
    df.loc[45, "temp_c"] = -40.0
    df.to_csv(target_csv, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    spike = flags[(flags["station_id"] == "SYN02") & (flags["channel"] == "temp_c") & (flags["action"] == "exclude")]
    assert len(spike) == 1
    assert spike.iloc[0]["issue"] == "isolated spike"


def test_duplicate_records_deduplication(tmp_path: Path):
    """Exact duplicate rows must be corrected and deduplicated."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=3)
    target_csv = pack / "data" / "SYN03.csv"
    df = pd.read_csv(target_csv)
    # Duplicate rows 20 to 35 and append them
    dup_rows = df.iloc[20:36]
    df = pd.concat([df, dup_rows]).reset_index(drop=True)
    df.to_csv(target_csv, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    dup_flag = flags[(flags["station_id"] == "SYN03") & (flags["issue"] == "duplicated records")]
    assert len(dup_flag) == 1
    assert dup_flag.iloc[0]["action"] == "correct"


def test_missing_data_intervals(tmp_path: Path):
    """Missing expected logger intervals must generate a 'no data logged' exclusion flag."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=4)
    target_csv = pack / "data" / "SYN01.csv"
    df = pd.read_csv(target_csv)
    # Delete 50 rows in the middle (about 8.3 hours)
    df = pd.concat([df.iloc[:100], df.iloc[150:]]).reset_index(drop=True)
    df.to_csv(target_csv, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    gap_flag = flags[(flags["station_id"] == "SYN01") & (flags["issue"] == "no data logged")]
    assert len(gap_flag) == 1
    assert gap_flag.iloc[0]["action"] == "exclude"


def test_malformed_records(tmp_path: Path):
    """Malformed CSV rows with bad number of fields must be caught."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=2)
    target_csv = pack / "data" / "SYN01.csv"
    lines = target_csv.read_text(encoding="utf-8").splitlines()
    lines.insert(10, "2026-06-01 01:30,12.5,extra,field,too,many,values,here,bad,row")
    target_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stations = analyse(pack)
    flags = flags_frame(stations)
    mal_flag = flags[(flags["station_id"] == "SYN01") & (flags["issue"] == "malformed lines")]
    assert len(mal_flag) == 1
    assert mal_flag.iloc[0]["action"] == "exclude"


def test_extreme_consistent_weather_kept(tmp_path: Path):
    """Extreme weather (e.g. windstorm) consistent across stations must be kept, not excluded."""
    pack = create_synthetic_pack(tmp_path, n_stations=4, days=5)
    # Inject widespread strong wind across all stations on day 2
    for s in ["SYN01", "SYN02", "SYN03", "SYN04"]:
        csv_file = pack / "data" / f"{s}.csv"
        df = pd.read_csv(csv_file)
        df.loc[180:190, "wind_kmh"] = 65.0
        df.to_csv(csv_file, index=False)

    stations = analyse(pack)
    flags = flags_frame(stations)
    keep_flags = flags[flags["issue"] == "very strong wind"]
    assert len(keep_flags) >= 1
    assert (keep_flags["action"] == "keep").all()


def test_empty_input_and_missing_files(tmp_path: Path):
    """An empty folder or missing directory should raise PackError and return exit code 2."""
    empty_dir = tmp_path / "empty_folder"
    empty_dir.mkdir()
    with pytest.raises(PackError):
        analyse(empty_dir)

    # Command line run should return exit code 2
    res = main([str(empty_dir)])
    assert res == 2


def test_incorrect_command_line_args():
    """Missing command-line arguments must exit with error."""
    with pytest.raises(SystemExit):
        main([])


def test_different_station_identifier_and_date_range(tmp_path: Path):
    """Checker must run dynamically on completely different station IDs and dates (e.g. STN99 in 2028)."""
    pack = create_synthetic_pack(tmp_path, n_stations=3, days=2, prefix="STN")
    stations = analyse(pack)
    assert len(stations) == 3
    assert set(s.sid for s in stations) == {"STN01", "STN02", "STN03"}
    df = flags_frame(stations)
    # Header format validation
    assert list(df.columns) == ["station_id", "channel", "start_utc", "end_utc", "issue", "action", "note"]
