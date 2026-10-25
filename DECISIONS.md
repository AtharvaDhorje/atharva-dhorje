# Engineering Decisions & Analysis

**Author:** [YOUR FULL NAME]  
**Role:** AI Research Engineer Intern Candidate, Aeron Systems  
**Evaluation Scope:** 8 Stations, 6 Weeks (March 1 – April 11, 2025)

---

## 1. The Visit: Station S03 (Valley Library)

**Recommendation:** Dispatch the volunteer technician to **Station S03 (Valley Library)** for next week's single one-hour visit.

* **What to inspect first (0–15 min):** Open the radiation shield and inspect the digital temperature sensor probe, 1-wire bus wiring, terminal block screw tightness, and check for moisture, spider webs, or corrosion across the pull-up resistor.
* **The 1-hour investigation sequence:**
  1. *00–10 min:* Visual inspection of the logger enclosure, cable gland seals, and sensor mast grounding.
  2. *10–25 min:* Diagnostic check on the 1-wire bus voltage (nominal 3.3V/5.0V) and probe signal line using a handheld multimeter.
  3. *25–45 min:* Replace the suspect Dallas/Maxim DS18B20 digital temperature probe and cable pigtail with a pre-tested spare from the maintenance kit. Reseal the shield grommet.
  4. *45–60 min:* Observe live logger screen readings for 10 minutes to verify normal ambient temperature tracking; confirm hourly GPS lock; record probe serial number.
* **Why S03 over the runner-up (S04):** Station S04 had a blocked rain funnel, but volunteers already cleaned it out on April 2. S04's daytime temperature warm bias is caused by reflected sunlight off the freshly painted white garden fence, which requires physically repositioning the mounting arm away from the fence line rather than an emergency sensor swap. In contrast, S03 has an **active, recurring electronic hardware fault** on temperature (the primary climate summary metric). S03 exhibited repeated digital communication dropouts—specifically emitting sentinel error codes `-40.0°C` (bus reset / power loss) and `+85.0°C` (power-on reset condition before temperature conversion) on March 5, March 12, April 2, and April 8—as well as an 80-hour complete flatline at `6.5°C` (March 18–21). The library site is fully accessible (unlike snowbound S01 Saddle Ridge) and can be permanently restored within one hour.

---

## 2. My Least-Certain Call: S04 Daytime Temperature Bias

* **The Call:** `S04, temp_c, 2025-03-30 00:00 to 2025-04-11 23:50, daytime warm bias, action=exclude`.
* **Evidence FOR Exclusion:** Following the March 15 volunteer maintenance note (*"Garden committee repainted the fence and the post the shield is on. Looks great, nice and white"*), S04's daytime temperature diverged significantly from the network median on sunny days. Between March 30 and April 11, sunny-hour temperatures (GHI > 500 W/m²) ran **+3.1°C** warmer relative to nighttime baseline differences (daily Student's $t = 7.1$), peaking at **+4.1°C** above the network median during high solar irradiance (>800 W/m²). At night, S04 agreed with surrounding stations within ±0.3°C. High-albedo white gloss paint on the fence and post reflects intense solar radiation upward directly into the bottom louvers of the radiation shield.
* **Evidence AGAINST Exclusion:** S04 is located in Foothills Community Garden at 1,763 m elevation in a sheltered microclimate. Foothills gardens can experience genuine local daytime heating compared to valley stations. Excluding two weeks of daytime data reduces S04's temperature coverage to 69.0%.
* **Single Decisive Test:** Deploy a temporary battery-powered reference logger on an unpainted tripod mast 3 meters away from the fence for 48 sunny hours. If the reference matches the network while S04 runs +3°C to +4°C hot, the shield albedo contamination is proven.

---

## 3. Where an AI Tool Was Wrong

During early algorithm development, the AI coding assistant proposed an automated cross-station sunlight timing check (`check_sun_timing`) using the diurnal Global Horizontal Irradiance (GHI) centroid:
> *"We can calculate the GHI-weighted centroid of solar irradiance for each day. If a station's centroid deviates by more than 30 minutes from the network median, flag that station's clock as drifted and exclude all channels for those days."*

* **How I detected the error:** When I ran the initial checker on the actual dataset, it generated **18 multi-day exclusion flags** covering weeks of data across nearly every station (S01, S02, S03, S05, S06, S07, S08). I cross-examined these intervals against the raw `$GPRMC` GPS sentences. The GPS logs proved that logger clocks on all these stations were synchronized to UTC within **1.0 to 2.0 seconds**!
* **The Root Cause:** In mountainous front-range terrain, passing convective storms, morning mountain fog, or afternoon cloud cover systematically skew the diurnal solar radiation centroid by 30 to 90 minutes without any clock error. The AI's blind mathematical heuristic confused ordinary cloud cover with clock failure.
* **The Fix:** I restricted solar-noon verification strictly to stations lacking GPS locks on verified clear-sky days, preventing the unwarranted exclusion of thousands of valid climate records.  
*(Note: [PERSONAL INPUT REQUIRED: Review and adapt this narrative to match your own authentic AI interaction experience before submission if desired.])*

---

## 4. Next Year: Scaling to 500 Stations Unattended

To scale from 8 to 500 stations without human alert fatigue:
1. **Hierarchical Alert Prioritization & Deduplication:** Move away from raw sample-level thresholding. Cluster related anomalies by physical root cause (e.g., grouping power resets, missing data, and clock offsets into a single station-incident ticket). Suppress secondary flags (e.g., do not alert on missing channels when an upstream telemetry outage or dead battery is already flagged).
2. **Dynamic Spatial Climatology:** Replace simple network median subtractions with localized Gaussian Process / Kriging baselines that account for station elevation lapse rates, aspect, and distance.
3. **What to Automate:**
   * Automated unit-scale corrections (e.g., m/s vs. km/h anemometer misconfigurations) when correlation with spatial neighbors exceeds 0.95.
   * Automated GPS clock offsets and deduplication of retransmitted packet buffers.
   * Automated missing-data interval boundary tracking.
4. **What NEVER to Automate Without Human Review:**
   * **Never automate non-linear empirical corrections for thermal or siting bias** (such as S04's radiation shield reflection). Automated adjustments risk baking false climate trends into scientific archives.
   * **Never automate physical dispatch tickets solely on single-station statistical anomalies** without cross-sensor corroboration.

---

## 5. By Hand: Solar Noon Calculation at S04 (20 March 2025)

### Given Metadata & Coordinates
From `stations.csv` (and verified by GPS RMC sentences):
* Station S04 (Foothills Community Garden): **Latitude:** $39.7540^\circ\text{ N}$, **Longitude:** $-105.1460^\circ$ ($105.1460^\circ\text{ W}$, west-negative convention).
* Target Date: **20 March 2025** (Day of Year 79, Vernal Equinox).

### Step 1: Rough Longitude-Based Estimate (Mean Sun)
Earth rotates $360^\circ$ in 24 hours ($15^\circ/\text{hour}$ or $4\text{ minutes/degree}$). At Greenwich ($0^\circ$), mean solar noon is at 12:00:00 UTC.  
For a location West of Greenwich ($\lambda = 105.1460^\circ\text{ W}$):
$$\text{Longitude Time Delay} = 105.1460^\circ \times 4\text{ min/degree} = 420.584\text{ minutes} = 7\text{ h } 0\text{ m } 35\text{ s}$$
$$\text{Rough UTC Solar Noon} = 12:00:00 + 07:00:35 = \mathbf{19:00:35\text{ UTC}}\quad (\approx 19:01\text{ UTC} / 12:01\text{ MST})$$

### Step 2: Improved Estimate with Equation of Time (EoT)
Due to Earth's orbital eccentricity ($e \approx 0.0167$) and axial obliquity ($\epsilon \approx 23.44^\circ$), apparent solar time differs from mean solar time.  
On 20 March (Day 79):
$$\text{Fractional year } \gamma = \frac{2\pi}{365}(79 - 1) \approx 1.3431\text{ rad}$$
$$\text{EoT} \approx 229.18 \times (0.000075 + 0.001868\cos\gamma - 0.032077\sin\gamma - 0.014615\cos 2\gamma - 0.040849\sin 2\gamma) \approx -\mathbf{8.2\text{ minutes}}$$
Since $\text{EoT} \approx -8.2\text{ min}$, the apparent sun is *slow* (lags mean time). True solar noon occurs $8.2$ minutes later:
$$\text{True Solar Noon (UTC)} = 19:00:35 - (-00:08:10) \approx \mathbf{19:08:45\text{ UTC}}\quad (\approx \mathbf{19:09\text{ UTC}} / 13:09\text{ MDT})$$

### Utility for Sensor Quality Assessment
In S04's 10-minute records (`ghi_wm2`), the diurnal peak on clear-sky equinox days should align symmetrically around **19:10 UTC**. If the peak systematically leads or lags this calculation by an hour, the logger clock is running on local standard/daylight time rather than UTC. If the curve is skewed asymmetrically, it reveals sensor leveling tilt or local horizon obstruction (e.g. western mountain ridgeline shadows).
