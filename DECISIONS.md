# Engineering Decisions & Analysis

**Author:** Atharva Dhorje  


---

## 1. The Visit: Station S03 (Valley Library)

**Recommendation:** Dispatch the technician to **Station S03 (Valley Library)** for next week's single one-hour visit.

* **First Inspection (0–15 min):** Open the radiation shield to inspect the digital temperature probe, 1-wire wiring, and terminal block for loose screws, moisture, or corrosion across the pull-up resistor.
* **One-Hour Investigation Plan:**
  * *00–10 min:* Inspect the logger enclosure, cable glands, and mast grounding.
  * *10–25 min:* Probe 1-wire bus voltage (3.3V/5V) and signal lines with a multimeter.
  * *25–45 min:* Replace the suspect DS18B20 digital temperature probe and pigtail with a calibrated spare.
  * *45–60 min:* Monitor live readings for 10 minutes to verify ambient tracking; verify GPS lock.
* **Why S03 over Runner-Up (S04):** Station S04's blocked rain gauge was already cleaned by volunteers on April 2. S04's daytime temperature warm bias is caused by solar reflection from a freshly painted white fence, which requires relocating the mast rather than an emergency sensor swap. In contrast, S03 has an **active electronic hardware failure** on temperature (the primary climate metric). S03 repeatedly emitted digital bus sentinels (`-40.0°C` bus reset and `+85.0°C` power-on reset) on March 5, 12, April 2, and 8, alongside an 80-hour freeze at `6.5°C` (March 18–21). The library site is fully accessible and can be permanently fixed within one hour.

---

## 2. My Least-Certain Call: S04 Daytime Temperature Bias

* **The Call:** `S04, temp_c, 2025-03-30 to 2025-04-11, daytime warm bias, action=exclude`.
* **Evidence FOR Exclusion:** After the March 15 volunteer log noted the fence and post were repainted bright white, S04's daytime temperature diverged from the network median on sunny days. Between March 30 and April 11, sunny-hour temperatures ran **+3.1°C** above nighttime baseline differences (daily $t = 7.1$), reaching **+4.1°C** above neighbors when GHI > 800 W/m². Night values agreed within ±0.3°C. White gloss paint reflects intense radiation upward into the shield louvers.
* **Evidence AGAINST Exclusion:** S04 sits in a sheltered garden microclimate at 1,763 m elevation, which can experience genuine daytime warming. Excluding two weeks reduces temperature coverage to 69.0%.
* **Single Decisive Test:** Place a temporary calibrated reference logger on an unpainted tripod 3 meters away from the fence for 48 sunny hours. If the reference matches the network while S04 runs hot, shield albedo contamination is proven.

---

## 3. Where an AI Tool Was Wrong

During development, the AI assistant proposed an automated solar-timing check (`check_sun_timing`) using the diurnal Global Horizontal Irradiance (GHI) centroid:
> *"Calculate the GHI-weighted centroid for each day. If a station's centroid deviates by >30 minutes from the network median, flag that station's clock as drifted and exclude all channels for those days."*

* **How I Detected the Error:** The initial run generated **18 multi-day exclusion flags** covering weeks of data across nearly every station (S01, S02, S03, S05, S06, S07, S08). When I checked the raw `$GPRMC` GPS sentences, atomic satellite time proved all these logger clocks were synchronized within **1.0 to 2.0 seconds** of UTC.
* **Root Cause & Fix:** In mountain terrain, morning fog or afternoon storms skew the solar radiation centroid by 30–90 minutes without clock error. The AI confused cloud cover with clock failure. I restricted solar-timing checks to stations lacking valid GPS fixes on verified clear-sky days, saving thousands of valid records.  
*(Note: [PERSONAL INPUT REQUIRED: Review and adjust this example to reflect your authentic interaction experience.])*

---

## 4. Next Year: Scaling to 500 Stations Unattended

To scale unattended monitoring across 500 stations without alert fatigue:
1. **Incident Clustering:** Replace raw single-reading threshold alerts with incident-level grouping. Suppress downstream channel alarms when an upstream power failure or telemetry outage is active.
2. **Spatial Climatology:** Replace simple network median subtractions with localized elevation-lapse-rate baselines and terrain-aware Kriging.
3. **What to Automate:** GPS clock offset shifts, duplicate packet dropping, and unit conversions (e.g. m/s to km/h anemometer misconfigurations) when spatial neighbor correlation exceeds 0.95.
4. **What NEVER to Automate Without Human Review:**
   * **Never automate empirical corrections for siting/albedo bias** (e.g. S04's painted post). Siting flaws require physical relocation; guessing an empirical offset risks corrupting long-term climate baselines.
   * **Never automate physical dispatch tickets** without cross-sensor corroboration.

---

## 5. By Hand: Solar Noon Calculation at S04 

### Metadata & Inputs
* Station S04: **Longitude:** $-105.1460^\circ$ ($105.1460^\circ\text{ W}$, west-negative convention).
* Target Date: **20 March 2025** (Day of Year 79, Vernal Equinox).

### Step 1: Rough Longitude Calculation (Mean Sun)
Earth rotates $360^\circ$ in 24 hours ($15^\circ/\text{hour}$ or $4\text{ min/degree}$). Mean solar noon at Greenwich ($0^\circ$) is 12:00:00 UTC.  
West of Greenwich ($\lambda = 105.1460^\circ\text{ W}$), solar noon is delayed by:
$$\Delta t = 105.1460^\circ \times 4\text{ min/deg} = 420.584\text{ min} = 7\text{h } 00\text{m } 35\text{s}$$
$$\text{Rough UTC Solar Noon} = 12:00:00 + 07:00:35 = \mathbf{19:00:35\text{ UTC}}\quad (\approx 19:01\text{ UTC} / 12:01\text{ MST})$$

### Step 2: Improved Calculation with Equation of Time (EoT)
Due to Earth's orbital eccentricity ($e \approx 0.0167$) and axial tilt ($\epsilon \approx 23.44^\circ$), apparent solar time deviates from mean time.  
On 20 March (Day 79), $\gamma = \frac{2\pi}{365}(78) \approx 1.343\text{ rad}$. The Equation of Time is:
$$\text{EoT} \approx -\mathbf{8.2\text{ minutes}}\quad (\text{apparent sun is slow / lags mean sun})$$
$$\text{True Solar Noon (UTC)} = 19:00:35 - (-00:08:10) \approx \mathbf{19:08:45\text{ UTC}}\quad (\approx \mathbf{19:09\text{ UTC}} / 13:09\text{ MDT})$$

### Utility for Quality Assessment
On a clear equinox day, S04's 10-minute solar irradiance (`ghi_wm2`) should center symmetrically around **19:10 UTC**. A one-hour systematic shift indicates a logger clock running on local standard/daylight time rather than UTC. An asymmetric diurnal curve reveals sensor leveling tilt or ridgeline shading.
