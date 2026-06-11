# 🦅 Fed Cascade v1.0

<div align="center">
  <p><strong>A Temporal Intelligence System for tracking U.S. Federal spending, USAID obligations, and global procurement flows to observable implementing organizations.</strong></p>
  
  ![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
  ![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)
  ![HTMX](https://img.shields.io/badge/HTMX-1.9.10-336699?logo=html5)
  ![Tailwind CSS](https://img.shields.io/badge/Tailwind-CSS-38B2AC?logo=tailwindcss)
  ![SQLite](https://img.shields.io/badge/Database-SQLite%20%2B%20SQLModel-003B57?logo=sqlite)
</div>

---

## 📜 The Manifesto

**Federal procurement data is not static. The first answer from official APIs is rarely the final answer.**

Every year, tens of billions of dollars move through U.S. federal agencies, defense contractors, and international NGOs. Traditional dashboards treat this data as gospel, constantly overwriting their records to show the "current" state of funding. 

But for OSINT analysts, journalists, and anti-corruption watchdogs, the real story isn't just *what exists* today. The real story is **what changed**. Old contracts get modified. Award amounts quietly swell or shrink. Organizations get renamed. Grants disappear retroactively. 

If official ledgers report $12 million delivered to a prime contractor in June, and quietly modify it to $27 million in December, most dashboards will only ever show you $27 million.

**Fed Cascade changes that.**

Fed Cascade is built as a **"Git-for-Procurement" version control system**. It doesn't just pull data; it snapshots it, stores it, and compares future data against your immutable baselines to detect retroactive anomalies. By illuminating the exact discrepancies between past and present official records from the U.S. Department of the Treasury, Fed Cascade turns basic reporting into actionable, temporal intelligence.

---

## 🚀 Core OSINT Innovations

### 👁️‍🗨️ The Watchdog Engine (Temporal Intelligence)
The crown jewel of the platform. Instead of blindly trusting live APIs, the engine allows you to set historical "Base Anchors." When you run subsequent live sweeps, the Diff Engine automatically detects:
* **Added Pipelines:** Retroactively injected funding flows or newly declassified contracts.
* **Modified Values:** Existing awards that have quietly changed (e.g., Original: $12M -> Current: $27M -> Delta: +$15M).
* **Removed Records:** Grants or contracts that have been scrubbed from the API.

### 🗄️ Git-Style Ledger & Additive Caching
The USAspending API heavily rate-limits and requires strict taxonomy filtering. Fed Cascade uses a **Safe Additive Merge (Time Bucket)** architecture. It merges new live API data into your local SQLite Factbook, keeping your existing offline records perfectly safe from false deletions. You can confidently build a 10-year immutable offline ledger.

### 🧠 Concurrent Global Sweeps (Entity Search)
Fed Cascade relies on an OSINT-style brute-force architecture: It automatically handles the complex multi-phase payload routing required by USAspending.gov, using background threads to fuzzy-search the raw JSON for any trace of your target entity across the globe, seamlessly merging domestic and international pipelines without ever locking the UI.

### 🏛️ The Macro & Micro Dissection
* **Macro Layer (Contracts):** Isolates massive prime contracts, definitive contracts, and purchase orders usually awarded to major for-profit implementing partners (the "Beltway Bandits").
* **Micro Layer (Grants/Loans):** Isolates downstream financial assistance, including project grants, direct payments, and loans flowing to international NGOs, UN agencies, and foreign governments.

### 🕵️‍♂️ Global Intelligence Dossiers (The Factbook)
* **Automated Profiling:** As you ingest data, Fed Cascade organically builds CIA Factbook-style dossiers for Prime Contractors, Grantees, Countries, and Sectors based purely on your offline cache. 
* **Sector Bibliographies:** Scans raw award descriptions globally for specific operational clusters (e.g., Cybersecurity, Defense Systems, Global Health, Infrastructure) and dynamically generates intelligence pivot tables.

---

## 🏗️ Data Architecture & Methodology

Data is sourced directly via asynchronous API calls (`httpx`) to the **USAspending.gov API v2**, managed by the U.S. Department of the Treasury.

### The Methodological Reality
Tracking money from a Federal Agency to a Prime Contractor is straightforward. Tracking it from that Prime Contractor to the ground is notoriously difficult. A standard international pipeline often looks like this:

`USAID → Prime Contractor / INGO → Sub-Awardee / Local Partner`

*Disclaimer: Fed Cascade tracks money to the furthest **observable** prime recipient published by USAspending. End-to-end visibility of final sub-contractors is often obscured or delayed by reporting agencies due to operational security concerns. Fed Cascade maps what is publicly claimed, and watches to see if those claims change.*

---

## 💻 Installation & Setup

Fed Cascade v1.0 requires **Python 3.10+**. 

### 1. Clone the repository
```bash
git clone https://github.com/bluetunax/fed-cascade.git
cd fed-cascade
```

### 2. Install Dependencies

**Option A: Using Conda (Recommended)**
```bash
conda env create -f environment.yml
conda activate fed-cascade
```

**Option B: Using Pip / Virtualenv**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Initialize the Engine
```bash
python main.py
```
*The app will automatically initialize the local `fed_cascade.db` SQLite database on first run.*

**Navigate to:** [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## 🗺️ System Blueprint

```text
fed-cascade/
├── fed_cascade.db                  # Local SQLite ledger (Auto-generated)
├── config.py                       # Pydantic Global Settings Management
├── main.py                         # FastAPI App & ASGI Server
├── models.py                       # SQLModel database schemas (Inc. LedgerChange)
├── data_engine.py                  # Async API fetchers & Taxonomy Router
├── db_manager.py                   # DB connection pooling, bulk inserts, exports
├── routers/                        # Modular FastAPI Endpoints
│   ├── dashboard.py                # Single-Year flows, JSON exports, wiping
│   ├── activities.py               # Text-based award description search
│   ├── profiles.py                 # 5-Year Pipelines & Entity dossiers
│   ├── intelligence.py             # Global search & Bibliography factbook
│   ├── version_control.py          # Diff engine & Ledger Hub merging
│   └── changes.py                  # Watchdog Volatility Dashboard
├── services/
│   ├── charts.py                   # Plotly dynamic visual generation
│   ├── intelligence.py             # Offline Factbook aggregation logic
│   ├── entity_normalization.py     # Federal Entity alias mapping and cleaning
│   ├── state.py                    # Real-time HTMX loading HUD state manager
│   └── version_control.py          # Diff calculation & Watchdog impact scoring
├── static/
│   └── style.css                   # Tailwind overrides & Vanilla Datatables
└── templates/                      # HTMX-powered Jinja2 UI Components
```

---

## 🛠️ Quick Start Guide

1. **Target a Zone:** Open the **Country Snapshot**, enter an ISO-3 code (e.g., `UKR`, `SDN`, `YEM`), a year, and click *Execute Query*.
2. **Lock an Anchor:** Navigate to **Ledger Version Control**. Find your new snapshot and click **Set Base**.
3. **Wait & Re-Scan:** Weeks or months later, return to the Country Snapshot. Check the **Force Live API Update** box and execute the query again.
4. **Track the Volatility:** The system will quietly pull the live data, bypass the strict USAspending taxonomy rules, execute a Safe Additive Merge to protect your local cache, and send any detected discrepancies to the **Watchdog Dashboard**. Navigate there to view and print the retroactive anomalies.
5. **Collaborate:** Navigate to **Settings** to export your entire immutable ledger as a portable JSON file to share with other analysts.

---

## 📄 License

**MIT License**

Copyright (c) 2026 Fed Cascade

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.