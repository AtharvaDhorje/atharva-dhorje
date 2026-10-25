Full Name: Atharva Dhorje
Email: atharva14122002@gmail.com
Video: https://youtu.be/rLetZaVPhVQ
Run: python checker.py "<path-to-data-folder>"

# Ridgeline Weather Station Network — Data Quality Checker & Health Assessment

This repository contains the complete solution for the **Aeron AI Research Engineer Intern Assignment**. It provides an automated, rule-based Python quality-assurance engine and network health evaluation for an 8-station volunteer weather monitoring network.

---

## 1. Project Purpose

The network coordinator (a retired teacher) must make two critical decisions on Monday:
1. **Which data can be trusted for the spring climate summary?**
2. **Which station should receive the single volunteer technician for one hour next week, and what should they inspect?**

This project provides a robust, deterministic Python checker (`checker.py`) that audits 6 weeks of 10-minute sensor data, GPS sentences, and volunteer notes, outputting an audited `flags.csv` and an executive printable summary (`report.html`).

---

## 2. Environment Setup & Installation

### Requirements
* **Python 3.11** or **3.12**
* Standard scientific libraries listed in `requirements.txt`:
  ```txt
  pandas>=2.2,<3.0
  numpy>=1.26
  openpyxl>=3.1
  pyarrow>=15.0
  pytest>=8.0
  ```

### Quickstart (PowerShell / Command Prompt)
1. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   *(Or on Linux/macOS: `source .venv/bin/activate`)*

2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

---

## 3. Running the Checker

The checker is executed with a single command passing the path to the unzipped data pack:

```bash
python checker.py "<path-to-data-folder>"
```

**Example:**
```powershell
python checker.py "ridgeline_data_pack (2)"
```

### Output Specification
* The program writes `flags.csv` into the **current working directory** (where the command was run), never modifying or writing inside the input data pack.
* If a custom output path is desired, use `-o <path>` (e.g., `python checker.py "<path>" -o my_flags.csv`).

---

## 4. Quality Checks Implemented

The checker employs transparent physical and statistical rules across seven channels (`temp_c`, `rh_pct`, `pressure_hpa`, `wind_kmh`, `wind_dir_deg`, `rain_mm`, `ghi_wm2`):

1. **GPS & Clock Integrity (`resolve_time`):**
   * Parses standard NMEA 0183 `$GPRMC` and `$GPGGA` sentences, verifying XOR checksums.
   * Compares logger clocks to satellite atomic time. Detects clock backward jumps (e.g., S06 restarting into Mountain Time UTC-6 following battery replacement).
2. **Deduplication & Record Ordering:**
   * Groups and removes exact duplicate retransmissions (e.g., S08 duplicated day) while preserving valid single intervals.
3. **Missing Data & Telemetry Dropouts:**
   * Reindexes each station onto a strict 10-minute UTC grid. Flags missing interval gaps (e.g., S08 cellular SIM card expiry).
4. **Physical Range & Electronic Sentinel Spikes:**
   * Detects impossible readings (e.g., temp > 60°C or RH > 100.5%).
   * Identifies isolated digital sensor error codes (e.g., Dallas 1-wire DS18B20 power-on sentinels `-40.0°C` and `+85.0°C`).
5. **Sensor Flatlines:**
   * Flags consecutive identical readings across varying weather conditions (e.g., S03 temperature freezing at `6.5°C` for 80 hours).
6. **Humidity Saturated-Stuck Detection:**
   * Flags hygrometer saturation (RH ≥ 99% sustained during sunny daytime conditions when network median RH is low).
7. **Cross-Station Barometric Calibration Steps:**
   * Reduces pressure to local median elevation using the barometric formula. Flags uncalibrated transducer replacements (e.g., S02 sensor board swap stepping +6.7 hPa).
8. **Anemometer Unit / Scale Calibration:**
   * Computes spatial wind velocity ratios against neighbors. Detects unit mismatch factors (e.g., S07 replacement anemometer reading m/s rather than km/h, factor 3.6).
9. **Solar Radiation Shield Contamination:**
   * Evaluates diurnal excess heating (sunny hours vs. nighttime baseline). Flags artificial solar heating caused by high-albedo paint on fence/post mounts (e.g., S04).
10. **Precipitation Blockage & Gauge Dumps:**
    * Identifies gauges clogged by debris/bird nests that record zero rainfall during widespread network storms, followed by sudden dry-weather water dumps.
11. **Extreme Weather Verification (`keep`):**
    * Verifies that severe wind peaks (up to 61 km/h) and heavy storm rainfall occurring across multiple stations are classified as genuine climate extremes, avoiding false-positive exclusions.

---

## 5. Definition of Flag Actions

The generated `flags.csv` strictly contains:
`station_id,channel,start_utc,end_utc,issue,action,note`

* **`exclude`**: Data is confirmed corrupted or physically impossible. Must be removed from the spring climate summary calculations.
* **`correct`**: The fault has an unassailable, mathematically reproducible correction explained in the `note` (e.g., subtracting a verified barometric offset, applying an hourly GPS clock shift, or multiplying by an anemometer unit conversion factor).
* **`keep`**: The observation is statistically unusual or extreme, but physical cross-station evidence confirms it is a genuine weather event (or high-altitude barometric baseline) that should remain in the scientific record.

---

## 6. Running Automated Tests

All automated unit and integration tests run against **purely synthetic test data** without embedding any proprietary data records:

```powershell
python -m pytest -v test_checker.py
```

### Test Coverage Summary:
* Valid synthetic data produces zero error flags.
* Injected sensor flatlines grouped into single interval flags.
* Physical range violations and isolated sentinel spikes (`-40°C`, `+85°C`).
* Duplicate timestamp retransmissions and deduplication.
* Telemetry gaps and missing logger intervals.
* Malformed CSV rows and corrupted NMEA checksums.
* Multi-station extreme windstorms preserved with `keep`.
* Missing files, empty input folders, and invalid CLI arguments.
* Dynamic execution across different station IDs and arbitrary date ranges.

---

## 7. Viewing the Network Health Report

The executive report is located at the repository root:
* **[report.html](report.html)**: Styled for A4 print layout (1 to 2 pages).
* To view: Double-click `report.html` in any web browser, or open it directly in VS Code / Chrome. Press **Ctrl + P** (Print) to generate a clean A4 PDF.

---

## 8. Limitations & Future Scalability

* **Complex Microclimate Terrain:** Current spatial comparisons use elevation-reduced medians. In steep alpine valleys, localized thermal inversions and ridge crest wind sheltering can cause genuine local variations.
* **Non-linear Thermal Corrections:** While S04's daytime warm bias (+3.1°C) is physically traced to high-albedo white paint on the fence post, we intentionally marked it as `exclude` rather than applying an uncertain empirical correction, preserving scientific data integrity.
* **Unattended 500-Station Scaling:** For large networks, manual alert inspection must be replaced with incident clustering and localized spatial Kriging baselines (detailed in `DECISIONS.md`, Question 4).
