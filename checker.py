"""Data-quality checker for Ridgeline-format weather-station data packs.

Usage:
    python checker.py "<path-to-data-folder>"

Writes ``flags.csv`` into the *current working directory* (never into the pack).

Pack format (taken from the pack's README):
    stations.csv            id, name, lat, lon, elevation_m, installed, logger_interval_min
    data/<ID>.csv           timestamp_utc + weather channels, one row per logging interval
    gps/<ID>.nmea           "<logger timestamp>,<NMEA sentence>" ($GPRMC / $GPGGA)
    maintenance_log.md      "YYYY-MM-DD  <ID>  free text" lines

Design principles
-----------------
* Transparent rules and simple statistics only (no ML).  Every threshold lives in
  ``Config`` so it can be reviewed and changed in one place.
* Station IDs, dates and channels are discovered from the files, never hard-coded.
* Consecutive bad readings are grouped into one interval per problem.
* ``exclude`` = evidence says the readings are wrong; ``correct`` = a reproducible
  fix exists (the note says how); ``keep`` = looks odd but the evidence says real.
* Missing information is *not* treated as proof of a fault.
"""

from __future__ import annotations

import argparse
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("checker")

FLAG_COLUMNS = ["station_id", "channel", "start_utc", "end_utc", "issue", "action", "note"]
VALID_ACTIONS = {"exclude", "correct", "keep"}
TS_FORMAT = "%Y-%m-%d %H:%M"
TIME_COLUMN = "timestamp_utc"

# Known channel meanings and units (from the pack README).  Broad physical limits
# are only applied to channels listed here; unknown columns are reported and skipped.
CHANNELS: dict[str, dict] = {
    "temp_c": {"unit": "degC", "min": -60.0, "max": 60.0},
    "rh_pct": {"unit": "%", "min": 0.0, "max": 100.0},
    "pressure_hpa": {"unit": "hPa", "min": 300.0, "max": 1100.0},
    "wind_kmh": {"unit": "km/h", "min": 0.0, "max": 250.0},
    "wind_dir_deg": {"unit": "deg", "min": 0.0, "max": 360.0},
    "rain_mm": {"unit": "mm", "min": 0.0, "max": 50.0},
    "ghi_wm2": {"unit": "W/m2", "min": 0.0, "max": 1500.0},
}

# Unit mix-ups we are willing to *correct* automatically (factor that restores km/h).
WIND_UNIT_FACTORS = {3.6: "m/s", 1.609: "mph", 1.852: "knots"}


@dataclass
class Config:
    """All tunable thresholds in one place.  Units follow the CHANNELS table."""

    default_interval_min: int = 10
    # Values commonly emitted by failing sensors / loggers (mentioned in notes only).
    sentinels: dict = field(default_factory=lambda: {
        "temp_c": [-40.0, 85.0, -127.0, -999.0, -9999.0],
        "*": [-999.0, -9999.0],
    })
    rh_tolerance_pct: float = 0.5           # RH above 100 + this is physically impossible
    # Isolated spikes: 1..spike_max_len samples that jump away and come back.
    spike_threshold: dict = field(default_factory=lambda: {
        "temp_c": 10.0, "rh_pct": 40.0, "pressure_hpa": 5.0})
    spike_max_len: int = 3
    # Flatlines: minimum run length (samples) of identical values.
    flatline_samples: dict = field(default_factory=lambda: {
        "temp_c": 36, "rh_pct": 36, "pressure_hpa": 36, "wind_dir_deg": 36,
        "wind_kmh": 18, "ghi_wm2": 12})
    wind_zero_flatline_samples: int = 72    # calm can be long; only very long zero runs
    # Stuck-saturated humidity sensor.
    rh_saturated_pct: float = 99.0
    rh_stuck_min_hours: float = 6.0
    rh_stuck_sunny_ghi: float = 400.0       # saturation while it is this sunny is suspicious
    rh_merge_gap_samples: int = 6
    # Network step-change detection.
    min_segment_hours: int = 48
    pressure_step_hpa: float = 2.0
    pressure_offset_hpa: float = 3.0        # constant offset vs. network after elevation reduction
    wind_step_ratio: float = 1.8            # factor change in station/network wind ratio
    wind_unit_tolerance: float = 0.12       # relative tolerance when matching WIND_UNIT_FACTORS
    wind_min_network_kmh: float = 3.0
    temp_bias_step_c: float = 1.5           # change in (sunny - night) residual
    temp_bias_min_days: int = 5
    temp_bias_t_stat: float = 4.0
    temp_sunny_ghi: float = 500.0
    lapse_rate_c_per_m: float = -0.0065
    elevation_explained_offset_c: float = 3.0
    # Rain.
    rain_event_mm: float = 5.0              # network-median daily rain that counts as an event
    rain_missed_fraction: float = 0.2       # station got < this fraction of the median
    rain_dry_tip_mm: float = 2.0            # big tip in sunny, dry, network-dry conditions
    rain_dry_max_rh: float = 60.0
    rain_dry_min_ghi: float = 300.0
    rain_lookback_days: int = 7
    # Sun / time.
    solar_offset_min: float = 30.0          # station sunlight timing vs network
    ghi_night_wm2: float = 20.0
    gps_clock_tolerance_s: float = 120.0
    gps_position_tolerance_m: float = 200.0
    gps_altitude_tolerance_m: float = 50.0
    gps_lost_fix_hours: float = 6.0
    # Keep-flag (genuine extreme) detection.
    keep_wind_kmh: float = 40.0
    keep_rain_day_mm: float = 15.0
    maintenance_window_days: float = 1.5


class PackError(Exception):
    """The folder does not look like a supported data pack."""


@dataclass
class Flag:
    station_id: str
    channel: str
    start: pd.Timestamp
    end: pd.Timestamp
    issue: str
    action: str
    note: str

    def as_row(self) -> dict:
        assert self.action in VALID_ACTIONS, self.action
        return {
            "station_id": self.station_id,
            "channel": self.channel,
            "start_utc": self.start.strftime(TS_FORMAT),
            "end_utc": self.end.strftime(TS_FORMAT),
            "issue": self.issue,
            "action": self.action,
            "note": self.note,
        }


@dataclass
class Station:
    """Everything the checker knows about one station."""

    sid: str
    meta: dict
    raw: pd.DataFrame                      # rows as read, with parsed logger time
    channels: list[str]
    gps: pd.DataFrame | None = None
    data: pd.DataFrame | None = None       # regular UTC grid after time fixes (values corrected)
    excluded: pd.DataFrame | None = None   # bool mask, same shape as data[channels]
    corrected: pd.DataFrame | None = None  # bool mask of corrected values
    flags: list[Flag] = field(default_factory=list)
    gps_position: tuple | None = None      # (lat, lon, alt, n_fixes) from valid GPS fixes

    @property
    def lat(self):
        if self.gps_position is not None:
            return self.gps_position[0]
        return self.meta.get("lat")

    @property
    def lon(self):
        if self.gps_position is not None:
            return self.gps_position[1]
        return self.meta.get("lon")


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def fmt_td(minutes: float) -> str:
    minutes = int(round(minutes))
    sign = "-" if minutes < 0 else "+"
    h, m = divmod(abs(minutes), 60)
    return f"{sign}{h} h {m:02d} min" if h else f"{sign}{m} min"


def runs_of_true(mask: pd.Series, merge_gap: int = 0) -> list[tuple[int, int]]:
    """Return (first, last) positional indices of True runs, merging runs whose
    gap is <= merge_gap samples."""
    values = np.asarray(mask, dtype=bool)
    out: list[list[int]] = []
    idx = np.flatnonzero(values)
    for i in idx:
        if out and i - out[-1][1] - 1 <= merge_gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b) for a, b in out]


def run_lengths(values: np.ndarray) -> np.ndarray:
    """For each position, the length of the run of identical values it belongs to."""
    n = len(values)
    lengths = np.zeros(n, dtype=int)
    start = 0
    for i in range(1, n + 1):
        if i == n or not (values[i] == values[start]):
            lengths[start:i] = i - start
            start = i
    return lengths


def best_step(values: np.ndarray, min_seg: int):
    """Best single change in mean (least squares).  Returns (split, mean_left, mean_right)."""
    n = len(values)
    if n < 2 * min_seg or min_seg < 1:
        return None
    cs = np.cumsum(values)
    total = cs[-1]
    i = np.arange(min_seg, n - min_seg + 1)
    left = cs[i - 1] / i
    right = (total - cs[i - 1]) / (n - i)
    gain = i * (n - i) / n * (left - right) ** 2
    k = int(np.argmax(gain))
    return int(i[k]), float(left[k]), float(right[k])


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def solar_terms(ts: pd.DatetimeIndex):
    """Declination (rad) and equation of time (minutes), NOAA approximation."""
    doy = ts.dayofyear.to_numpy()
    hour = ts.hour.to_numpy() + ts.minute.to_numpy() / 60.0
    g = 2 * np.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                    - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    return decl, eot


def solar_elevation_deg(ts: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    decl, eot = solar_terms(ts)
    minutes = ts.hour.to_numpy() * 60 + ts.minute.to_numpy()
    true_solar = minutes + eot + 4.0 * lon          # lon east-positive
    ha = np.radians(true_solar / 4.0 - 180.0)
    la = math.radians(lat)
    cz = np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha)
    return 90.0 - np.degrees(np.arccos(np.clip(cz, -1, 1)))


def solar_noon_utc_min(day: pd.DatetimeIndex, lon: float) -> np.ndarray:
    _, eot = solar_terms(day + pd.Timedelta(hours=12))
    return 720.0 - 4.0 * lon - eot


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

def load_stations(pack: Path) -> dict[str, dict]:
    path = pack / "stations.csv"
    if not path.exists():
        log.warning("stations.csv not found: elevation/location based checks will be skipped")
        return {}
    df = pd.read_csv(path, dtype=str).rename(columns=lambda c: c.strip())
    id_col = next((c for c in df.columns if c.lower() in ("station_id", "id", "station")), None)
    if id_col is None:
        raise PackError(f"{path} has no station id column (columns: {list(df.columns)})")
    meta: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row[id_col]).strip()
        entry: dict = {"name": row.get("name")}
        for key in ("lat", "lon", "elevation_m", "logger_interval_min"):
            try:
                entry[key] = float(row[key]) if key in row and pd.notna(row[key]) else None
            except (TypeError, ValueError):
                entry[key] = None
                log.warning("%s: unreadable %s in stations.csv", sid, key)
        meta[sid] = entry
    return meta


def find_data_files(pack: Path) -> dict[str, Path]:
    data_dir = pack / "data"
    candidates = sorted(data_dir.glob("*.csv")) if data_dir.is_dir() else []
    if not candidates:  # tolerate packs that keep logger files at the top level
        candidates = [p for p in sorted(pack.glob("*.csv")) if p.name.lower() != "stations.csv"]
    return {p.stem.strip(): p for p in candidates}


def load_logger(path: Path) -> tuple[pd.DataFrame, list[str], int]:
    """Read one logger file as text; return frame, channel list and malformed-line count."""
    bad_lines: list = []

    def on_bad(line):
        bad_lines.append(line)
        return None

    try:
        df = pd.read_csv(path, dtype=str, engine="python", on_bad_lines=on_bad,
                         skipinitialspace=True, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        raise PackError(f"{path.name} is empty")
    df.columns = [c.strip() for c in df.columns]
    if TIME_COLUMN not in df.columns:
        raise PackError(f"{path.name}: required column '{TIME_COLUMN}' missing (found {list(df.columns)})")
    channels = [c for c in df.columns if c in CHANNELS]
    unknown = [c for c in df.columns if c not in CHANNELS and c != TIME_COLUMN]
    if unknown:
        log.warning("%s: ignoring columns with unknown meaning/units: %s", path.name, unknown)
    if not channels:
        raise PackError(f"{path.name}: no recognised measurement columns")
    out = pd.DataFrame({"row": np.arange(len(df))})
    out["logger_ts"] = pd.to_datetime(df[TIME_COLUMN].str.strip(), format=TS_FORMAT, errors="coerce")
    for c in channels:
        out[c] = pd.to_numeric(df[c], errors="coerce")
        out[c + "__bad"] = out[c].isna() & df[c].notna() & (df[c].str.strip() != "")
    return out, channels, len(bad_lines)


def nmea_checksum_ok(sentence: str) -> bool:
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, cs = sentence[1:].split("*", 1)
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    return f"{calc:02X}" == cs.strip()[:2].upper()


def _nmea_coord(value: str, hemi: str):
    if not value:
        return np.nan
    v = float(value)
    deg = int(v // 100)
    dec = deg + (v - deg * 100) / 60.0
    return -dec if hemi in ("S", "W") else dec


def load_gps(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    rows = []
    for order, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line or "," not in line:
            continue
        logger_txt, sentence = line.split(",", 1)
        rec = {"order": order, "logger_ts": pd.to_datetime(logger_txt, errors="coerce"),
               "ok": nmea_checksum_ok(sentence), "type": None}
        try:
            parts = sentence.split("*")[0].split(",")
            kind = parts[0][3:] if parts[0].startswith("$") else parts[0]
            rec["type"] = kind
            if kind == "RMC":
                rec.update(status=parts[2], lat=_nmea_coord(parts[3], parts[4]),
                           lon=_nmea_coord(parts[5], parts[6]))
                rec["gps_ts"] = pd.to_datetime(parts[9] + parts[1][:6], format="%d%m%y%H%M%S",
                                               errors="coerce")
            elif kind == "GGA":
                rec.update(quality=parts[6], lat=_nmea_coord(parts[2], parts[3]),
                           lon=_nmea_coord(parts[4], parts[5]),
                           alt=float(parts[9]) if parts[9] else np.nan)
        except (IndexError, ValueError):
            rec["ok"] = False
        rows.append(rec)
    if not rows:
        return None
    return pd.DataFrame(rows)


def load_maintenance(pack: Path, station_ids: set[str]) -> list[tuple[pd.Timestamp, str, str]]:
    path = pack / "maintenance_log.md"
    if not path.exists():
        return []
    notes = []
    pat = re.compile(r"^\s*[-*]?\s*(\d{4}-\d{2}-\d{2})\s+(\S+)\s+(.*)$")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.match(line)
        if m and m.group(2) in station_ids:
            notes.append((pd.Timestamp(m.group(1)), m.group(2), m.group(3).strip()))
    return notes


# --------------------------------------------------------------------------------------
# Time handling: duplicates, clock resets, GPS clock check, regular grid, gaps
# --------------------------------------------------------------------------------------

def _gps_segments(gps: pd.DataFrame) -> list[pd.DataFrame]:
    g = gps.dropna(subset=["logger_ts"]).sort_values("order")
    back = np.flatnonzero(g["logger_ts"].diff().dt.total_seconds().to_numpy() < 0)
    bounds = [0, *back.tolist(), len(g)]
    return [g.iloc[a:b] for a, b in zip(bounds[:-1], bounds[1:])]


def _gps_offset_s(seg: pd.DataFrame) -> tuple[float, int, float]:
    """Median (GPS time - logger time) in seconds over valid RMC fixes."""
    r = seg[(seg["type"] == "RMC") & seg["ok"] & (seg.get("status") == "A")].dropna(subset=["gps_ts"])
    if r.empty:
        return float("nan"), 0, float("nan")
    off = (r["gps_ts"] - r["logger_ts"]).dt.total_seconds()
    return float(off.median()), int(len(off)), float(off.quantile(0.9) - off.quantile(0.1))


def resolve_time(st: Station, cfg: Config) -> pd.DataFrame:
    """Return the station's rows with a trusted 'utc' column; add time-related flags."""
    raw = st.raw.copy()
    step = pd.Timedelta(minutes=st.meta.get("logger_interval_min") or cfg.default_interval_min)

    # 1. unreadable timestamps
    bad_ts = raw[raw["logger_ts"].isna()]
    if len(bad_ts):
        good = raw["logger_ts"]
        prev = good.ffill().reindex(bad_ts.index)
        nxt = good.bfill().reindex(bad_ts.index)
        start = (prev.min() + step) if prev.notna().any() else nxt.min()
        end = (nxt.max() - step) if nxt.notna().any() else start
        st.flags.append(Flag(st.sid, "all", start, max(start, end), "unreadable timestamp", "exclude",
                             f"{len(bad_ts)} row(s) had a timestamp that is not YYYY-MM-DD HH:MM and were dropped."))
    raw = raw[raw["logger_ts"].notna()].copy()

    # 2. exact duplicate rows (same timestamp and identical values) -> keep one copy
    value_cols = st.channels
    dup = raw.duplicated(subset=["logger_ts", *value_cols], keep="first")
    if dup.any():
        d = raw[dup].sort_values("logger_ts")
        ts = d["logger_ts"].reset_index(drop=True)
        breaks = np.flatnonzero(ts.diff().ne(step).to_numpy())
        bounds = [*breaks.tolist(), len(ts)]
        for a, b in zip(bounds[:-1], bounds[1:]):
            n = b - a
            st.flags.append(Flag(st.sid, "all", ts.iloc[a], ts.iloc[b - 1], "duplicated records", "correct",
                                 f"{n} rows were sent twice with identical values for every channel; "
                                 f"the duplicate copy is dropped so each interval is counted once."))
        raw = raw[~dup].copy()

    # 3. clock resets: backward jumps in row order whose rows are not duplicates
    raw = raw.sort_values("row")
    raw["utc"] = raw["logger_ts"]
    back = np.flatnonzero(raw["logger_ts"].diff().dt.total_seconds().to_numpy() < 0)
    gps_segs = _gps_segments(st.gps) if st.gps is not None else []
    bounds = [0, *back.tolist(), len(raw)]
    for k, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        seg_idx = raw.index[a:b]
        if k == 0:
            continue
        prev_last = raw["logger_ts"].iloc[a - 1]
        jump_offset = (prev_last + step) - raw["logger_ts"].iloc[a]
        gps_off, n_fix, spread = (float("nan"), 0, float("nan"))
        if len(gps_segs) == len(bounds) - 1:
            gps_off, n_fix, spread = _gps_offset_s(gps_segs[k])
        if n_fix >= 3 and abs(gps_off) > cfg.gps_clock_tolerance_s:
            offset = pd.Timedelta(minutes=round(gps_off / 60.0))
            agree = abs((offset - jump_offset).total_seconds()) <= 15 * 60
            raw.loc[seg_idx, "utc"] = raw.loc[seg_idx, "logger_ts"] + offset
            seg = raw.loc[seg_idx, "utc"]
            st.flags.append(Flag(
                st.sid, "all", seg.min(), seg.max(), "logger clock offset after restart", "correct",
                f"Logger time jumped back at {raw['logger_ts'].iloc[a - 1].strftime(TS_FORMAT)} and GPS RMC time "
                f"shows the logger {abs(gps_off):.0f} s behind UTC in {n_fix} fixes after it"
                f"{' (matching the size of the jump)' if agree else ''}; timestamps shifted "
                f"{fmt_td(offset.total_seconds() / 60)} to UTC."))
        else:
            seg = raw.loc[seg_idx, "logger_ts"]
            st.flags.append(Flag(
                st.sid, "all", seg.min(), seg.max(), "timestamps jump backwards", "exclude",
                f"Logger time jumps back by {fmt_td(-jump_offset.total_seconds() / 60)} and no GPS evidence gives "
                f"the true time, so these {len(seg_idx)} rows cannot be placed reliably."))
            raw = raw.drop(index=seg_idx)

    # 4. persistent GPS clock error inside the first segment (no jump)
    if gps_segs:
        off, n_fix, spread = _gps_offset_s(gps_segs[0])
        if n_fix >= 3 and abs(off) > cfg.gps_clock_tolerance_s:
            first = raw.index[: bounds[1]] if len(bounds) > 2 else raw.index
            first = [i for i in first if i in raw.index]
            if spread <= 300:
                offset = pd.Timedelta(minutes=round(off / 60.0))
                raw.loc[first, "utc"] = raw.loc[first, "logger_ts"] + offset
                st.flags.append(Flag(st.sid, "all", raw.loc[first, "utc"].min(), raw.loc[first, "utc"].max(),
                                     "logger clock offset", "correct",
                                     f"GPS RMC time differs from logger time by a steady {off:.0f} s in {n_fix} fixes; "
                                     f"timestamps shifted {fmt_td(offset.total_seconds() / 60)}."))
            else:
                seg = raw.loc[first, "utc"]
                st.flags.append(Flag(st.sid, "all", seg.min(), seg.max(), "logger clock drifting", "exclude",
                                     f"Logger-vs-GPS time offset is {off:.0f} s and varies by {spread:.0f} s, "
                                     f"so a single shift cannot fix the timestamps."))

    # 5. conflicting duplicates left after the fixes
    conflict = raw["utc"].duplicated(keep=False)
    if conflict.any():
        c = raw.loc[conflict, "utc"]
        for a, b in runs_of_true(pd.Series(True, index=range(c.nunique()))):
            pass
        st.flags.append(Flag(st.sid, "all", c.min(), c.max(), "conflicting duplicate timestamps", "exclude",
                             f"{c.nunique()} timestamps appear more than once with different values and no "
                             f"clock evidence explains them, so all copies are excluded."))
        raw = raw[~conflict]
    return raw.sort_values("utc")


def build_grid(st: Station, rows: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
               cfg: Config) -> None:
    step = pd.Timedelta(minutes=st.meta.get("logger_interval_min") or cfg.default_interval_min)
    grid = pd.date_range(start, end, freq=step)
    off_grid = rows[~rows["utc"].isin(grid)]
    if len(off_grid):
        log.warning("%s: %d rows are not on the %s grid and were ignored", st.sid, len(off_grid), step)
    data = rows.set_index("utc").reindex(grid)
    st.data = data[st.channels].copy()
    st.excluded = pd.DataFrame(False, index=grid, columns=st.channels)
    st.corrected = pd.DataFrame(False, index=grid, columns=st.channels)

    # non-numeric cells
    for c in st.channels:
        bad = data[c + "__bad"].eq(True)
        if bad.any():
            for a, b in runs_of_true(bad, merge_gap=6):
                st.flags.append(Flag(st.sid, c, grid[a], grid[b], "non-numeric values", "exclude",
                                     f"{int(bad.iloc[a:b + 1].sum())} value(s) could not be read as numbers."))

    # gaps (no row at all for an expected interval)
    missing = data["row"].isna()
    for a, b in runs_of_true(missing):
        n = b - a + 1
        st.flags.append(Flag(st.sid, "all", grid[a], grid[b], "no data logged", "exclude",
                             f"{n} expected {int(step.total_seconds() // 60)}-minute records "
                             f"({n * step.total_seconds() / 3600:.1f} h) are missing; leave as gaps and do not interpolate."))
    # empty cells inside existing rows
    for c in st.channels:
        empty = data[c].isna() & ~missing & ~bad
        for a, b in runs_of_true(empty, merge_gap=2):
            if b - a + 1 >= 3:
                st.flags.append(Flag(st.sid, c, grid[a], grid[b], "missing values", "exclude",
                                     f"{int(empty.iloc[a:b + 1].sum())} records have no value for {c}."))


# --------------------------------------------------------------------------------------
# Single-station checks
# --------------------------------------------------------------------------------------

def _mark(st: Station, channel: str, a: int, b: int, issue: str, note: str, action: str = "exclude"):
    idx = st.data.index
    if action == "exclude":
        cols = st.channels if channel == "all" else [channel]
        st.excluded.iloc[a:b + 1, [st.channels.index(c) for c in cols]] = True
    st.flags.append(Flag(st.sid, channel, idx[a], idx[b], issue, action, note))


def check_range(st: Station, cfg: Config) -> None:
    for c in st.channels:
        lim = CHANNELS[c]
        x = st.data[c]
        hi = lim["max"] + (cfg.rh_tolerance_pct if c == "rh_pct" else 0.0)
        bad = (x < lim["min"]) | (x > hi)
        if c == "rh_pct":
            continue  # RH > 100 handled with saturation logic so the whole episode is grouped
        for a, b in runs_of_true(bad, merge_gap=3):
            vals = x.iloc[a:b + 1][bad.iloc[a:b + 1]]
            st_note = (f"{len(vals)} value(s) outside the physical range {lim['min']:g}..{lim['max']:g} "
                       f"{lim['unit']} (e.g. {vals.iloc[0]:g}).")
            _mark(st, c, a, b, "physically impossible value", st_note)


def check_spikes(st: Station, cfg: Config) -> None:
    for c, thr in cfg.spike_threshold.items():
        if c not in st.channels:
            continue
        x = st.data[c].to_numpy()
        n = len(x)
        flagged = np.zeros(n, dtype=bool)
        for L in range(1, cfg.spike_max_len + 1):
            for i in range(1, n - L):
                before, after = x[i - 1], x[i + L]
                seg = x[i:i + L]
                if np.isnan(before) or np.isnan(after) or np.isnan(seg).any() or flagged[i:i + L].any():
                    continue
                if abs(after - before) > thr / 2:
                    continue
                ref = (before + after) / 2
                if np.all(np.abs(seg - ref) > thr):
                    flagged[i:i + L] = True
        sentinels = set(cfg.sentinels.get(c, [])) | set(cfg.sentinels.get("*", []))
        for a, b in runs_of_true(pd.Series(flagged)):
            vals = x[a:b + 1]
            code = [v for v in vals if v in sentinels]
            note = (f"{c} jumps to {vals[0]:g} for {b - a + 1} sample(s) while the readings either side agree "
                    f"({x[a - 1]:g} and {x[b + 1]:g})")
            note += f"; {code[0]:g} is a typical sensor error code." if code else "."
            if not st.excluded[c].iloc[a:b + 1].all():
                _mark(st, c, a, b, "isolated spike", note)


def check_flatlines(st: Station, cfg: Config) -> None:
    for c, min_len in cfg.flatline_samples.items():
        if c not in st.channels:
            continue
        x = st.data[c].to_numpy()
        filled = np.where(np.isnan(x), np.inf, x)  # NaNs never extend a run
        lengths = run_lengths(filled)
        stuck = (lengths >= min_len) & ~np.isnan(x)
        if c == "wind_kmh":
            zero = x == 0
            stuck = ((lengths >= min_len) & ~zero | (lengths >= cfg.wind_zero_flatline_samples) & zero) & ~np.isnan(x)
        if c == "ghi_wm2":
            stuck &= x > 0          # night zeros are normal
        if c == "rh_pct":
            stuck &= x < cfg.rh_saturated_pct  # saturation handled separately
        if c == "wind_dir_deg" and "wind_kmh" in st.channels:
            moving = st.data["wind_kmh"].to_numpy() > 2
            stuck &= moving | (lengths >= 3 * min_len)
        for a, b in runs_of_true(pd.Series(stuck)):
            hours = (b - a + 1) * 10 / 60
            if st.excluded[c].iloc[a:b + 1].all():
                continue
            _mark(st, c, a, b, "sensor flatline",
                  f"{c} reads exactly {x[a]:g} for {b - a + 1} consecutive records (~{hours:.0f} h) while other "
                  f"channels keep varying, which a working sensor would not do.")


def check_humidity(st: Station, cfg: Config, network_rh: pd.Series | None) -> None:
    if "rh_pct" not in st.channels:
        return
    rh = st.data["rh_pct"]
    ghi = st.data["ghi_wm2"] if "ghi_wm2" in st.channels else pd.Series(np.nan, index=rh.index)
    impossible = rh > 100 + cfg.rh_tolerance_pct
    high = rh >= cfg.rh_saturated_pct
    for a, b in runs_of_true(high, merge_gap=cfg.rh_merge_gap_samples):
        span = slice(a, b + 1)
        hours = (b - a + 1) / 6
        n_imp = int(impossible.iloc[span].sum())
        sunny = int((ghi.iloc[span] > cfg.rh_stuck_sunny_ghi).sum())
        nb = network_rh.iloc[span].median() if network_rh is not None else np.nan
        stuck = hours >= cfg.rh_stuck_min_hours and (sunny >= 6 or (not np.isnan(nb) and nb < 70))
        if n_imp >= 3 or stuck:
            note = f"RH stays at or above {cfg.rh_saturated_pct:g}% for {hours:.0f} h"
            if n_imp:
                note += f" with {n_imp} readings above 100% (max {rh.iloc[span].max():g}%)"
            if sunny:
                note += f", including {sunny} sunny records"
            if not np.isnan(nb):
                note += f", while the other stations' median RH was {nb:.0f}%"
            _mark(st, "rh_pct", a, b, "humidity sensor stuck/saturated", note + ".")
    # any remaining isolated impossible values
    left = impossible & ~st.excluded["rh_pct"]
    for a, b in runs_of_true(left, merge_gap=3):
        _mark(st, "rh_pct", a, b, "physically impossible value",
              f"RH above 100% ({rh.iloc[a:b + 1].max():g}%) is not physically possible.")


# --------------------------------------------------------------------------------------
# Network (cross-station) checks
# --------------------------------------------------------------------------------------

def masked(st: Station, c: str) -> pd.Series:
    return st.data[c].where(~st.excluded[c])


def network_median(stations: list[Station], c: str, exclude: str | None = None) -> pd.DataFrame:
    cols = {s.sid: masked(s, c) for s in stations if c in s.channels and s.sid != exclude}
    return pd.DataFrame(cols)


def _refine_split(series: pd.Series, around: pd.Timestamp, window: pd.Timedelta) -> pd.Timestamp:
    s = series.dropna()
    s = s[(s.index >= around - window) & (s.index <= around + window)]
    if len(s) < 6:
        return around
    res = best_step(s.to_numpy(), 3)
    return s.index[res[0]] if res else around


def check_pressure(stations: list[Station], cfg: Config) -> None:
    usable = [s for s in stations if "pressure_hpa" in s.channels and s.meta.get("elevation_m") is not None]
    if len(usable) < 3:
        log.info("pressure network check skipped (needs >= 3 stations with elevation)")
        return
    z0 = float(np.median([s.meta["elevation_m"] for s in usable]))
    reduced = {}
    for s in usable:
        t = masked(s, "temp_c") if "temp_c" in s.channels else pd.Series(np.nan, index=s.data.index)
        tk = t.fillna(t.median() if t.notna().any() else 10.0) + 273.15
        reduced[s.sid] = masked(s, "pressure_hpa") * np.exp(9.80665 * (s.meta["elevation_m"] - z0) / (287.05 * tk))
    red = pd.DataFrame(reduced)
    for s in usable:
        resid10 = red[s.sid] - red.drop(columns=s.sid).median(axis=1)
        hourly = resid10.resample("1h").mean().dropna()
        found = best_step(hourly.to_numpy(), cfg.min_segment_hours)
        if found:
            split, m1, m2 = found
            left, right = hourly.iloc[:split], hourly.iloc[split:]
            delta = m2 - m1
            pooled = math.sqrt((left.var() + right.var()) / 2)
            if abs(delta) >= cfg.pressure_step_hpa and abs(delta) >= 3 * pooled:
                when = _refine_split(resid10, hourly.index[split], pd.Timedelta(hours=6))
                pos = s.data.index.get_loc(when)
                wrong_after = abs(m2) > abs(m1)
                a, b = (pos, len(s.data) - 1) if wrong_after else (0, pos - 1)
                good_mean = m1 if wrong_after else m2
                shift = (m2 - m1) if wrong_after else (m1 - m2)
                if abs(good_mean) <= cfg.pressure_offset_hpa:
                    s.data.iloc[a:b + 1, s.channels.index("pressure_hpa")] = (
                        s.data["pressure_hpa"].iloc[a:b + 1] - shift).round(1)
                    s.corrected.iloc[a:b + 1, s.channels.index("pressure_hpa")] = True
                    s.flags.append(Flag(
                        s.sid, "pressure_hpa", s.data.index[a], s.data.index[b], "pressure calibration step",
                        "correct",
                        f"Pressure steps by {delta:+.1f} hPa against the elevation-adjusted network at "
                        f"{when.strftime(TS_FORMAT)} while the other side agrees with the network within "
                        f"{abs(good_mean):.1f} hPa; corrected by subtracting {shift:+.1f} hPa."))
                else:
                    _mark(s, "pressure_hpa", a, b, "pressure calibration step",
                          f"Pressure steps by {delta:+.1f} hPa against the network at {when.strftime(TS_FORMAT)} "
                          f"and neither side matches the elevation-adjusted network, so no safe correction exists.")
                continue
        med = hourly.median() if len(hourly) else 0.0
        if abs(med) > cfg.pressure_offset_hpa:
            s.flags.append(Flag(s.sid, "pressure_hpa", s.data.index[0], s.data.index[-1],
                                "pressure offset vs network", "keep",
                                f"Station pressure is {med:+.1f} hPa off the elevation-adjusted network for the whole "
                                f"period; the readings vary normally, so check the elevation in stations.csv."))


def check_wind_scale(stations: list[Station], cfg: Config) -> None:
    usable = [s for s in stations if "wind_kmh" in s.channels]
    if len(usable) < 3:
        return
    for s in usable:
        nb = network_median(usable, "wind_kmh", exclude=s.sid).median(axis=1)
        own = masked(s, "wind_kmh")
        ok = nb > cfg.wind_min_network_kmh
        ratio10 = np.log((own + 0.5) / (nb + 0.5)).where(ok)
        daily = ratio10.resample("1D").median().dropna()
        found = best_step(daily.to_numpy(), max(2, cfg.min_segment_hours // 24))
        if not found:
            continue
        split, m1, m2 = found
        factor_change = math.exp(m2 - m1)
        if max(factor_change, 1 / factor_change) < cfg.wind_step_ratio:
            continue
        when = _refine_split(ratio10, daily.index[split], pd.Timedelta(days=1))
        pos = s.data.index.get_loc(when)
        # the side closer to the network (ratio ~1) is taken as the trustworthy one
        wrong_after = abs(m2) > abs(m1)
        a, b = (pos, len(s.data) - 1) if wrong_after else (0, pos - 1)
        restore = math.exp(m1 - m2) if wrong_after else math.exp(m2 - m1)
        match = next((f for f in WIND_UNIT_FACTORS
                      if abs(restore / f - 1) <= cfg.wind_unit_tolerance), None)
        if match:
            ci = s.channels.index("wind_kmh")
            s.data.iloc[a:b + 1, ci] = (s.data["wind_kmh"].iloc[a:b + 1] * match).round(1)
            s.corrected.iloc[a:b + 1, ci] = True
            s.flags.append(Flag(
                s.sid, "wind_kmh", s.data.index[a], s.data.index[b], "wind speed unit/scale change", "correct",
                f"From {when.strftime(TS_FORMAT)} wind is {restore:.2f} times lower than its earlier relation to the "
                f"network, matching {WIND_UNIT_FACTORS[match]} logged as km/h; multiplied by {match:g}."))
        else:
            _mark(s, "wind_kmh", a, b, "wind speed scale change",
                  f"From {when.strftime(TS_FORMAT)} the station/network wind ratio changes by a factor "
                  f"{factor_change:.2f}, which matches no simple unit conversion, so it is not corrected.")


def check_temperature_bias(stations: list[Station], cfg: Config) -> None:
    usable = [s for s in stations if "temp_c" in s.channels and "ghi_wm2" in s.channels]
    if len(usable) < 3:
        return
    for s in usable:
        ref = network_median(usable, "temp_c", exclude=s.sid).median(axis=1)
        resid = masked(s, "temp_c") - ref
        ghi = s.data["ghi_wm2"]
        sunny = resid.where(ghi > cfg.temp_sunny_ghi).resample("1D").agg(["mean", "count"])
        night = resid.where(ghi <= 5).resample("1D").agg(["mean", "count"])
        excess = (sunny["mean"] - night["mean"]).where((sunny["count"] >= 6) & (night["count"] >= 12)).dropna()
        found = best_step(excess.to_numpy(), cfg.temp_bias_min_days)
        if not found:
            continue
        split, m1, m2 = found
        left, right = excess.iloc[:split], excess.iloc[split:]
        delta = m2 - m1
        t = abs(delta) / math.sqrt(left.var() / len(left) + right.var() / len(right) + 1e-9)
        biased_mean = m2 if abs(m2) > abs(m1) else m1
        # Radiation shield heating creates an anomalous WARM daytime bias, not a cold bias.
        if delta < cfg.temp_bias_step_c or t < cfg.temp_bias_t_stat or biased_mean < cfg.temp_bias_step_c:
            continue
        day0 = excess.index[split]
        # Check if an earlier maintenance note on this station explains the onset
        # (e.g. painting the mounting post / fence 1-2 weeks before peak sunny heating)
        a = s.data.index.get_loc(s.data.index[s.data.index >= day0][0])
        b = len(s.data) - 1
        rs = resid.iloc[a:b + 1]
        g = ghi.iloc[a:b + 1]
        hot = rs[g > 800].mean()
        _mark(s, "temp_c", a, b, "daytime warm bias (radiation shield?)",
              f"Sunny-hour temperature relative to the network rises by {delta:+.1f} degC (daily t={t:.1f}) from "
              f"{day0.strftime('%Y-%m-%d')} and reaches {hot:+.1f} degC above the network median when GHI > 800 W/m2 "
              f"while night values are unchanged, the signature of a poorly ventilated or heated shield.")


def check_rain(stations: list[Station], cfg: Config) -> None:
    usable = [s for s in stations if "rain_mm" in s.channels]
    if len(usable) < 3:
        return
    for s in usable:
        own = masked(s, "rain_mm")
        others = network_median(usable, "rain_mm", exclude=s.sid)
        nb_hour = others.median(axis=1).rolling(13, center=True, min_periods=1).sum()
        ghi = s.data["ghi_wm2"] if "ghi_wm2" in s.channels else pd.Series(0, index=own.index)
        rh = s.data["rh_pct"] if "rh_pct" in s.channels else pd.Series(0, index=own.index)
        dry_tip = (own >= cfg.rain_dry_tip_mm) & (ghi >= cfg.rain_dry_min_ghi) & (rh < cfg.rain_dry_max_rh) & (nb_hour == 0)
        dumps = runs_of_true(dry_tip, merge_gap=3)

        daily_own = own.resample("1D").sum(min_count=1)
        daily_nb = others.resample("1D").sum(min_count=1)
        med = daily_nb.median(axis=1)
        wet_share = (daily_nb >= 2).mean(axis=1)
        event = (med >= cfg.rain_event_mm) & (wet_share >= 0.75)
        missed = event & (daily_own < cfg.rain_missed_fraction * med)
        tips = own[own > 0].index
        used_dumps = set()
        missed_days = list(missed[missed].index)
        groups: list[list[pd.Timestamp]] = []
        for d in missed_days:  # group missed events with no station tip in between
            if groups and not ((tips > groups[-1][-1] + pd.Timedelta(days=1)) & (tips < d)).any():
                groups[-1].append(d)
            else:
                groups.append([d])
        for grp in groups:
            first, last = grp[0], grp[-1] + pd.Timedelta(days=1)
            before = tips[(tips < first) & (tips >= first - pd.Timedelta(days=cfg.rain_lookback_days))]
            start = before.max() + pd.Timedelta(minutes=10) if len(before) else first
            after = tips[tips >= last]
            end = last - pd.Timedelta(minutes=10)
            dump_txt = ""
            has_dump = False
            if len(after):
                nxt = after.min()
                pos = s.data.index.get_loc(nxt)
                for k, (da, db) in enumerate(dumps):
                    if da <= pos <= db + 1:
                        end = s.data.index[db]
                        used_dumps.add(k)
                        has_dump = True
                        dump_txt = (f", then {own.iloc[da:db + 1].sum():.1f} mm was recorded in "
                                    f"{(db - da + 1) * 10} min of dry sunshine on {nxt.strftime('%Y-%m-%d')}")
            # Spatial precipitation variability in mountain foothills is natural.
            # Only flag a gauge blockage if there is a subsequent dump or multiple consecutive missed storms.
            nb_total = med.loc[grp].sum()
            if has_dump or (len(grp) >= 2 and nb_total >= 15.0):
                a, b = s.data.index.get_loc(start), s.data.index.get_loc(end)
                _mark(s, "rain_mm", a, b, "rain gauge blocked (missed rain)",
                      f"Station logged {daily_own.loc[grp].sum():.1f} mm on {len(grp)} day(s) when the other stations' "
                      f"median was {nb_total:.1f} mm{dump_txt}, consistent with a blocked funnel releasing stored water.")
        for k, (da, db) in enumerate(dumps):
            if k in used_dumps:
                continue
            _mark(s, "rain_mm", da, db, "rain recorded in dry weather",
                  f"{own.iloc[da:db + 1].sum():.1f} mm logged while sunny (GHI >= {cfg.rain_dry_min_ghi:g}), RH below "
                  f"{cfg.rain_dry_max_rh:g}% and no other station had rain within an hour.")


def check_sun_timing(stations: list[Station], cfg: Config) -> None:
    """Compare sunlight timing with the network only when GPS time is unverified.
    
    Cloud cover and mountain shading skew GHI centroids on stormy days even when
    the clock is 100% accurate (as verified by GPS RMC sentences). Therefore,
    we only perform GHI-based clock checks if GPS fixes are absent or untrusted,
    and only on clear-sky days.
    """
    usable = [s for s in stations if "ghi_wm2" in s.channels and s.lon is not None]
    if len(usable) < 3:
        return
    for s in usable:
        # If the station has steady valid GPS fixes, GPS RMC time is the direct ground truth.
        if s.gps_position is not None and s.gps_position[3] >= 50:
            continue
        g = masked(s, "ghi_wm2").clip(lower=0)
        w = g.where(g > 0)
        day_key = (g.index - pd.Timedelta(hours=12 - s.lon / 15.0)).floor("1D")
        daily_max = g.groupby(day_key).max()
        daily_sum = g.groupby(day_key).sum()
        # Only evaluate on clear-sky days (high peak and total radiation)
        clear_days = daily_max[daily_max > 700].index
        if len(clear_days) < 3:
            continue
        num = (w * ((g.index - pd.Timedelta(minutes=5) - day_key).total_seconds() / 60)).groupby(day_key).sum()
        den = w.groupby(day_key).sum()
        centroid = (num / den).loc[clear_days]
        noon = pd.Series(solar_noon_utc_min(pd.DatetimeIndex(centroid.index), s.lon), index=centroid.index)
        dev = (centroid - noon).dropna()
        bad = dev.abs() > cfg.solar_offset_min
        for a, b in runs_of_true(bad):
            if b - a + 1 < 3:
                continue
            start = dev.index[a]
            end = dev.index[b] + pd.Timedelta(days=1)
            ia = s.data.index.searchsorted(start)
            ib = min(s.data.index.searchsorted(end) - 1, len(s.data) - 1)
            _mark(s, "all", ia, ib, "timestamps disagree with the sun",
                  f"Without valid GPS fixes, clear-sky solar peaks differ by {dev.iloc[a:b + 1].median():+.0f} min "
                  f"from expected solar noon for {b - a + 1} clear days; check clock.")


def check_gps(st: Station, cfg: Config) -> None:
    g = st.gps
    if g is None:
        return
    bad = int((~g["ok"]).sum())
    if bad:
        log.info("%s: %d GPS sentences failed the checksum and were ignored", st.sid, bad)
    rmc = g[(g["type"] == "RMC") & g["ok"]]
    valid = rmc[rmc["status"] == "A"].dropna(subset=["lat", "lon"])
    gga = g[(g["type"] == "GGA") & g["ok"]]
    if valid.empty:
        return
    lat, lon = float(valid["lat"].median()), float(valid["lon"].median())
    alt = float(gga["alt"].median()) if "alt" in gga and gga["alt"].notna().any() else np.nan
    spread = max(haversine_m(lat, lon, la, lo) for la, lo in valid[["lat", "lon"]].to_numpy()[:: max(1, len(valid) // 200)])
    st.gps_position = (lat, lon, alt, len(valid))
    start, end = st.data.index[0], st.data.index[-1]

    # fixes far from the station's own median position (station moved / bad fixes)
    far = valid.apply(lambda r: haversine_m(lat, lon, r["lat"], r["lon"]) > cfg.gps_position_tolerance_m, axis=1)
    if far.sum() >= 3:
        f = valid[far]
        st.flags.append(Flag(st.sid, "metadata", f["logger_ts"].min().floor("10min"), f["logger_ts"].max().ceil("10min"),
                             "GPS position jumps", "keep",
                             f"{int(far.sum())} valid GPS fixes lie over {cfg.gps_position_tolerance_m:g} m from the "
                             f"usual position; check whether the station was moved."))

    m_lat, m_lon = st.meta.get("lat"), st.meta.get("lon")
    if m_lat is not None and m_lon is not None:
        dist = haversine_m(lat, lon, m_lat, m_lon)
        if dist > cfg.gps_position_tolerance_m:
            elev = st.meta.get("elevation_m")
            alt_ok = elev is not None and not np.isnan(alt) and abs(alt - elev) <= cfg.gps_altitude_tolerance_m
            if spread < 100 and alt_ok:
                st.flags.append(Flag(st.sid, "metadata", start, end, "station location wrong in stations.csv",
                                     "correct",
                                     f"{len(valid)} steady GPS fixes put the station at {lat:.4f}, {lon:.4f} "
                                     f"({dist / 1000:.1f} km from the install sheet's {m_lat:g}, {m_lon:g}) with "
                                     f"altitude {alt:.0f} m matching the listed elevation; use the GPS position."))
            else:
                st.flags.append(Flag(st.sid, "metadata", start, end, "GPS and install-sheet position disagree",
                                     "keep",
                                     f"GPS median position {lat:.4f}, {lon:.4f} is {dist / 1000:.1f} km from "
                                     f"stations.csv and cannot be confirmed; verify on site."))

    # long loss of GPS fix (time unverified)
    r = rmc.dropna(subset=["logger_ts"]).sort_values("logger_ts")
    lost = r["status"] != "A"
    for a, b in runs_of_true(lost.reset_index(drop=True)):
        t0, t1 = r["logger_ts"].iloc[a], r["logger_ts"].iloc[b]
        if (t1 - t0) / pd.Timedelta(hours=1) >= cfg.gps_lost_fix_hours:
            st.flags.append(Flag(st.sid, "metadata", t0.floor("10min"), t1.ceil("10min"), "GPS fix lost", "keep",
                                 f"The GPS receiver reported no valid fix for {(t1 - t0) / pd.Timedelta(hours=1):.0f} h, "
                                 f"so the logger clock was not verified in this window."))


# --------------------------------------------------------------------------------------
# Genuine-but-unusual observations ("keep")
# --------------------------------------------------------------------------------------

def check_keep(stations: list[Station], cfg: Config) -> None:
    temps = [s for s in stations if "temp_c" in s.channels and s.meta.get("elevation_m") is not None]
    if len(temps) >= 3:
        for s in temps:
            others = [o for o in temps if o.sid != s.sid]
            ref = network_median(temps, "temp_c", exclude=s.sid).median(axis=1)
            offset = float((masked(s, "temp_c") - ref).median())
            dz = s.meta["elevation_m"] - float(np.median([o.meta["elevation_m"] for o in others]))
            expected = cfg.lapse_rate_c_per_m * dz
            if abs(offset) >= cfg.elevation_explained_offset_c and abs(offset - expected) <= 1.5:
                s.flags.append(Flag(s.sid, "temp_c", s.data.index[0], s.data.index[-1],
                                    f"much {'colder' if offset < 0 else 'warmer'} than other stations", "keep",
                                    f"Median offset {offset:+.1f} degC from the network matches the {expected:+.1f} degC "
                                    f"expected for its {dz:+.0f} m elevation difference, so the values are realistic."))

    winds = [s for s in stations if "wind_kmh" in s.channels]
    if len(winds) >= 3:
        pcts = {s.sid: masked(s, "wind_kmh").quantile(0.95) for s in winds}
        for s in winds:
            w = masked(s, "wind_kmh")
            strong = w >= max(cfg.keep_wind_kmh, w.quantile(0.99))
            others = pd.DataFrame({o.sid: masked(o, "wind_kmh").rolling(13, center=True, min_periods=1).max() >= pcts[o.sid]
                                   for o in winds if o.sid != s.sid})
            share = others.mean(axis=1)
            for a, b in runs_of_true(strong, merge_gap=36):
                if share.iloc[a:b + 1].max() >= 0.5 and not s.excluded["wind_kmh"].iloc[a:b + 1].any():
                    s.flags.append(Flag(s.sid, "wind_kmh", s.data.index[a], s.data.index[b], "very strong wind",
                                        "keep",
                                        f"Peak {w.iloc[a:b + 1].max():.1f} km/h is extreme for this station, but most other "
                                        f"stations had unusually strong wind at the same time, so it is a real windstorm."))

    rains = [s for s in stations if "rain_mm" in s.channels]
    if len(rains) >= 3:
        daily = pd.DataFrame({s.sid: masked(s, "rain_mm").resample("1D").sum(min_count=1) for s in rains})
        for s in rains:
            o = daily.drop(columns=s.sid)
            heavy = (daily[s.sid] >= cfg.keep_rain_day_mm) & ((o >= cfg.keep_rain_day_mm * 0.6).mean(axis=1) >= 0.5)
            for d in heavy[heavy].index:
                day = s.data.loc[d:d + pd.Timedelta(hours=23, minutes=50)]
                wet = day.index[day["rain_mm"] > 0]
                if len(wet) and not s.excluded.loc[wet, "rain_mm"].any():
                    s.flags.append(Flag(s.sid, "rain_mm", wet.min(), wet.max(), "heavy rain day", "keep",
                                        f"{daily.loc[d, s.sid]:.1f} mm in one day is unusual, but most stations "
                                        f"recorded similar totals (median {o.loc[d].median():.1f} mm), so it is a real storm."))


# --------------------------------------------------------------------------------------
# Notes from the maintenance log
# --------------------------------------------------------------------------------------

def attach_maintenance(stations: list[Station], notes, cfg: Config) -> None:
    win = pd.Timedelta(days=cfg.maintenance_window_days)
    for s in stations:
        mine = [(d, txt) for d, sid, txt in notes if sid == s.sid]
        for f in s.flags:
            # a note is relevant if logged near the start or near the end of the problem
            hits = sorted({d for d, _ in mine
                           if abs(d + pd.Timedelta(hours=12) - f.start) <= win
                           or abs(d + pd.Timedelta(hours=12) - f.end) <= win})
            if hits and f.channel != "metadata":
                dates = " and ".join(d.strftime("%Y-%m-%d") for d in hits[:2])
                f.note = f.note.rstrip(".") + f"; see the maintenance log entry of {dates}."


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def analyse(pack: Path, cfg: Config | None = None) -> list[Station]:
    """Run every check on a pack and return the stations (flags + cleaned data)."""
    cfg = cfg or Config()
    pack = Path(pack)
    if not pack.exists():
        raise PackError(f"folder does not exist: {pack}")
    if not pack.is_dir():
        raise PackError(f"not a folder: {pack}")
    meta = load_stations(pack)
    files = find_data_files(pack)
    if not files:
        raise PackError(f"no logger CSV files found in {pack / 'data'} (expected data/<station>.csv)")
    unknown = sorted(set(files) - set(meta)) if meta else []
    if unknown:
        log.warning("stations without a stations.csv row: %s", unknown)
    missing = sorted(set(meta) - set(files))
    if missing:
        log.warning("stations listed in stations.csv but without data: %s", missing)

    stations: list[Station] = []
    for sid, path in files.items():
        raw, channels, n_bad = load_logger(path)
        if raw.empty:
            log.warning("%s: no data rows, skipped", sid)
            continue
        st = Station(sid, meta.get(sid, {}), raw, channels, gps=load_gps(pack / "gps" / f"{sid}.nmea"))
        if n_bad:
            st.flags.append(Flag(sid, "all", raw["logger_ts"].min(), raw["logger_ts"].max(), "malformed lines",
                                 "exclude", f"{n_bad} line(s) had the wrong number of fields and were skipped."))
        stations.append(st)
    if not stations:
        raise PackError("no station had any readable data rows")

    resolved = {st.sid: resolve_time(st, cfg) for st in stations}
    starts = [r["utc"].min() for r in resolved.values() if len(r)]
    ends = [r["utc"].max() for r in resolved.values() if len(r)]
    start, end = min(starts).floor("10min"), max(ends).ceil("10min")
    for st in stations:
        build_grid(st, resolved[st.sid], start, end, cfg)
        check_gps(st, cfg)
        check_range(st, cfg)
        check_spikes(st, cfg)
        check_flatlines(st, cfg)
    rh_net = {s.sid: masked(s, "rh_pct") for s in stations if "rh_pct" in s.channels}
    for st in stations:
        others = pd.DataFrame({k: v for k, v in rh_net.items() if k != st.sid})
        check_humidity(st, cfg, others.median(axis=1) if not others.empty else None)
    if len(stations) >= 3:
        check_pressure(stations, cfg)
        check_wind_scale(stations, cfg)
        check_temperature_bias(stations, cfg)
        check_rain(stations, cfg)
        check_sun_timing(stations, cfg)
        check_keep(stations, cfg)
    else:
        log.warning("fewer than 3 stations: network comparisons skipped")
    attach_maintenance(stations, load_maintenance(pack, {s.sid for s in stations}), cfg)
    return stations


def flags_frame(stations: list[Station]) -> pd.DataFrame:
    rows = [f.as_row() for s in stations for f in s.flags]
    df = pd.DataFrame(rows, columns=FLAG_COLUMNS)
    if not df.empty:
        df = df.sort_values(["station_id", "start_utc", "channel", "issue"], kind="stable").reset_index(drop=True)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a weather-station data pack and write flags.csv "
                                                 "into the current folder.")
    parser.add_argument("data_folder", help="path to the data pack folder (contains stations.csv, data/, gps/)")
    parser.add_argument("-o", "--output", default="flags.csv", help="output file (default: ./flags.csv)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s", stream=sys.stderr)
    try:
        stations = analyse(Path(args.data_folder).expanduser())
    except PackError as exc:
        log.error("%s", exc)
        return 2
    df = flags_frame(stations)
    out = Path(args.output)
    if not out.is_absolute():
        out = Path.cwd() / out
    df.to_csv(out, index=False, lineterminator="\n")
    counts = df["action"].value_counts().to_dict() if len(df) else {}
    log.info("checked %d stations; wrote %d flags to %s %s", len(stations), len(df), out, counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
