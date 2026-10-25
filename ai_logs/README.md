# AI Logs & Conversation Export Instructions

Per the assignment guidelines, this directory must contain your **genuine AI conversation exports** (e.g. ChatGPT / Gemini / Claude share links or exported PDFs, GitHub Copilot chat JSON, or IDE interaction transcripts).

---

## 1. How to Export Your AI Conversation Logs

Before submitting the assignment, save your authentic chat histories into this `ai_logs/` directory:

* **For Web-based AI Tools (ChatGPT / Gemini / Claude):**
  1. Open the conversation thread used for this project.
  2. Click the **Share** button to copy the public shareable link, OR press **Ctrl + P** in your browser and select **Save as PDF**.
  3. Save the PDF file into this folder (e.g., `ai_logs/chat_transcript.pdf`).
* **For GitHub Copilot / Cursor / IDE Assistants:**
  1. Export or copy the chat session history into a markdown or text file (e.g., `ai_logs/copilot_chat.md`).
* **Guidelines from Aeron:**
  > *"Messy is fine. Don't tidy them: we want to see how you worked, including the dead ends."*  
  Do not fabricate artificial chats.

---

## 2. Three-Minute Video Screen Recording Guide

The assignment requires a **3-minute screen recording with your voice** (unlisted YouTube or Google Drive link in `README.md`).

### Suggested 3-Minute Presentation Plan:

* **0:00 – 0:30 (Introduction & Executive Summary):**
  * Open `report.html` in your web browser.
  * State your name, the role (AI Research Engineer Intern candidate), and the core coordinator decision: 98.7% data usable, 5 automated corrections, and recommendation to visit S03.
* **0:30 – 1:20 (The Strongest Verified Flag — Station S07 Wind Anemometer Swap):**
  * Open `flags.csv` and show line for `S07, wind_kmh, 2025-03-26`.
  * Explain the evidence: The volunteer maintenance log on March 26 recorded an anemometer swap with a different brand. Cross-station comparison showed S07's recorded wind speed immediately dropped by a factor of 3.6 relative to neighboring stations. This matched the exact unit conversion from m/s to km/h ($1\text{ m/s} = 3.6\text{ km/h}$). Multiplying by 3.6 restored the full valid time series.
* **1:20 – 2:05 (The Least-Certain Flag — Station S04 Fence Albedo Heating):**
  * Show `S04, temp_c, daytime warm bias`.
  * Explain the tension: On March 15, the garden committee painted the fence and mounting post bright white. Sunny-hour temperatures ran +3.1°C to +4.1°C hotter than neighbors, while night baseline agreed within 0.3°C. It was excluded to protect the climate summary, but an on-site reference logger is needed to definitively separate local garden microclimate from shield radiation contamination.
* **2:05 – 2:40 (Technician Visit — Station S03 Valley Library):**
  * Return to `report.html` and highlight Section 2 (The Visit).
  * Explain why S03: Unlike S02, S06, and S07 (which were 100% fixed in software), S03 has an active electronic hardware failure on temperature—emitting digital error codes (-40°C and +85°C) and freezing for 80 hours. Walk through the realistic 1-hour schedule (visual check, 1-wire bus voltage diagnostic, probe swap, 10-minute live verification).
* **2:40 – 3:00 (Engineering Rigor & Wrap-up):**
  * Switch to terminal, run `pytest -v test_checker.py` showing all 12 unit tests passing against synthetic data.
  * Mention Question 3 from `DECISIONS.md`: How you caught and fixed the AI's naive GHI solar-centroid heuristic that would have thrown away good data on cloudy days.
