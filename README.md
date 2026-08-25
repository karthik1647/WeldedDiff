# WeldedDiff: Three-Way Ledger Reconciliation & Forensic Auditor

WeldedDiff is an automated three-way ledger reconciliation system built for the Razorpay AI Buildathon (AI Finance Controller Track). It matches internal e-commerce sales records against payment gateway settlement ledgers and bank statement deposit feeds down to 0.01 INR decimal precision.

The system uses a two-phase architecture:
1. **Deterministic Matching Engine:** Resolves exact order-to-payout mappings, contract fee compliance (2% MDR + 18% GST), and T+2 day settlement SLA windows.
2. **Probabilistic Forensic Auditor (LLM Layer):** Uses DeepSeek to evaluate fuzzy bank statement narrations, UTR cross-references, and batch settlement aggregations for unmatched records.

---

### Data Layer Architecture & Razorpay API Integration

#### Schema-Accurate Sandbox Data
The transaction generator is built against authentic Razorpay API response schemas (`/v1/orders`, `/v1/payments`, `/v1/settlements`). 

During development, real test-mode API key generation on Razorpay's Dashboard was blocked by an account KYC verification flow. To maintain execution without blocking, the data pipeline uses a local Razorpay Sandbox Mock Client (`src/razorpay_client.py`). This client generates schema-accurate JSON payloads matching Razorpay's exact REST API response structures (including `entity`, `amount` in paise, `fee`, `tax`, `utr`, `status`, and `created_at` timestamps).

* **Razorpay API Schema Features:** Order ID schemas (`ord_...`), Payment ID schemas (`pay_...`), contractual fee calculations (2% MDR + 18% GST), and settlement UTR references.
* **Injected Anomalies (Synthetic Noise):**
  * **Batch Settlement Aggregations:** 10 individual payments consolidated into 1 bank statement deposit line (~111,422.86 INR).
  * **MDR Fee Overcharges:** Payment `pay_005` charged 3.0% MDR instead of the contractual 2.0%.
  * **Timing SLA Delays:** Friday payments (`pay_025`) settling Tuesday in the bank feed.
  * **Split Checkouts:** Order `ord_040` split into two 50% payments (`pay_040_a` and `pay_040_b`).

---

### Deterministic Safety Guardrails (False-Positive Prevention)

A critical requirement of financial reconciliation is preventing false-positive commits. WeldedDiff enforces a **Confidence Safety Gate** (threshold $\ge 85\%$) and a **Deterministic Safety Override**.

#### Case Study: Payment `pay_012`
* **Scenario:** Payment `pay_012` has an expected net payout of 9,378.32 INR. The bank statement contains a bulk deposit line of 111,422.86 INR with narration `CMS/RAZORPAY APY WELDEDDIFFA/set_101/BATCH`.
* **LLM Evaluation:** DeepSeek detected matching settlement ID `set_101` in the narration and proposed a match with **95% confidence**.
* **Safety Gate Override:** The deterministic safety check intercepted the proposal. Because the bank credit amount (111,422.86 INR) differed from the single payout net (9,378.32 INR) by more than 100.00 INR and lacked a explicit payment UTR, the safety filter overrode the LLM's 95% confidence score and forced an **Abstained** status.
* **Result:** `pay_012` was safely pushed to the Exception queue for manual operational review rather than corrupting the ledger.

---

### Performance Metrics (Dataset of 102 Payouts)

* **Total Transactions Reviewed:** 119 (100 orders + 19 LLM audit calls)
* **Naive Baseline Match Rate:** 1.96% (2 / 102 payouts matched)
* **Pipeline Match Rate (Deterministic + LLM):** 81.37% (83 / 102 payouts matched)
* **Auto-Committed Records:** 84 (83 payouts + 1 refund debit)
* **Abstained Exceptions:** 19 (18 low-confidence batch deposits + 1 safety gate override)
* **LLM Audit Execution Cost:** $0.0106 USD across 19 API calls (9,213 input tokens, 2,530 output tokens)

---

### Honest Technical Limitations

1. **Batch Settlement Disambiguation:** Bank clearing house transfers (NEFT/RTGS) aggregate multiple payouts into single bulk credits. When bank statements contain only batch IDs (`set_101/BATCH`) without itemized UTRs, the LLM layer correctly abstains from 1-to-1 matching to prevent ledger corruption.
2. **Sandbox API Execution:** The pipeline runs against local schema-accurate Razorpay Sandbox data rather than authenticated live API endpoints due to merchant KYC verification constraints.

---

### Project Structure & File Inventory

```
WeldedDiff/
├── server.py                        # FastAPI web backend server serving REST API & static SPA
├── static/
│   └── index.html                   # HTML/CSS/JS frontend with live stage-by-stage transaction replay
├── src/
│   ├── razorpay_client.py           # Authentic Razorpay API Sandbox Mock client
│   ├── generator.py                 # Transaction dataset & anomaly generator
│   ├── engine.py                    # Deterministic matching & fee calculation engine
│   ├── auditor.py                   # DeepSeek LLM forensic auditor (structured JSON outputs)
│   ├── pipeline.py                  # Orchestration pipeline with safety gate enforcement
│   └── utils.py                     # Decimal rounding and text cleanup utilities
├── tests/
│   └── test_engine.py               # Pytest unit test suite (6 passing tests)
├── scripts/
│   └── run_determinism_check.py     # Determinism verification script (asserts 100% execution consistency)
├── traces/
│   ├── decision_log.json            # Step-by-step decision trace history
│   └── summary_report.json          # KPI summary metrics
├── postmortems/
│   └── case_01_duplicate_collision.md # Incident postmortem for duplicate payment refund collision
└── requirements.txt                 # Core dependencies
```

---

### Running the Application

1. **Install Dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Run the FastAPI Server:**
   ```powershell
   python server.py
   ```
   Open **`http://localhost:8000`** in your browser to view the interactive Live Replay Dashboard.

3. **Run Unit Tests:**
   ```powershell
   python -m pytest tests/
   ```

4. **Run Determinism Verification:**
   ```powershell
   python -m scripts.run_determinism_check
   ```
