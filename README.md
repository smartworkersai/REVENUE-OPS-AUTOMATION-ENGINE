# 🚀 Revenue-Ops-Automation-Engine

An autonomous lead-generation and market-data extraction engine designed to streamline high-velocity outreach and operational workflows using Claude 3.5 Sonnet and Playwright.

## 🛠️ Core Capabilities
* **Dynamic Ingestion:** Custom Python-based scrapers with JA3/TLS fingerprint spoofing to bypass advanced anti-bot measures.
* **LLM-Driven Orchestration:** Integrates Anthropic's Claude (Tool Use + Pydantic v2) for context-aware data structuring and personalized asset generation.
* **Robust State Management:** 9-stage SQLite state machine with crash recovery and asynchronous logging to Google Sheets.
* **Enterprise Integration:** Automated Gmail OAuth handling for OTP verification and Telegram-based real-time alerting.

## ⚙️ Tech Stack
* **Engine:** Python 3.12, Sync Playwright, Camoufox
* **Intelligence:** Anthropic Claude 3.5 Sonnet
* **Persistence:** SQLite (WAL mode), Google Sheets API
* **Infrastructure:** Decodo Residential Proxies, APScheduler
