Full Name: REPLACE WITH YOUR FULL NAME
Email: REPLACE WITH YOUR EMAIL
Video: ADD YOUR RECORDING LINK BEFORE SUBMISSION
Run: python checker.py "PATH_TO_DATA_PACK"

# Aeron assignment — starter project

## Status
This is a starting scaffold, not a finished assignment. At this stage, `checker.py` lists files, prints basic details of supported tables, and creates an empty `flags.csv`. It does not yet identify or classify data-quality problems.

## Setup (Windows PowerShell)
1. Install Python 3.11 or 3.12 from https://www.python.org/downloads/ and tick **Add Python to PATH** during installation.
2. Open this folder in VS Code. Install the Microsoft Python extension if prompted.
3. Open **Terminal → New Terminal** in VS Code.
4. Create a virtual environment:
   `py -m venv .venv`
5. Activate it in PowerShell:
   `.\.venv\Scripts\Activate.ps1`
6. If PowerShell blocks activation, use Command Prompt in VS Code and run:
   `.venv\Scripts\activate.bat`
7. Install packages:
   `python -m pip install -r requirements.txt`
8. Run the starter checker, replacing the example with the real path to the data folder:
   `python checker.py "C:\path\to\your\data_pack"`

The starter writes `flags.csv` in the current terminal folder (this project folder if you opened the terminal here). Read the terminal output to learn the data file names and column names before adding checks.

## Privacy
Do not copy the supplied data pack into this repository or commit it to GitHub. Keep it in a separate local folder. The assignment says the pack must not be shared.

## Before submission
- Implement and test actual quality checks; the starter `flags.csv` is empty.
- Generate and verify the final flags against the supplied pack.
- Create `report.html` or `report.pdf` based on real findings.
- Write `DECISIONS.md` in your own words.
- Keep complete AI-chat exports in `ai_logs/`.
- Record the required three-minute screen recording and add its link above.
