# WeldedDifff: Hybrid Transaction Reconciliation & Forensic Audit Engine

WeldedDifff is a transaction reconciliation and audit engine designed for multi-gateway merchant settlements. It matches transactions across internal order ledgers, gateway reports, and bank statements using a hybrid deterministic-probabilistic pipeline.

---

## The Problem: Fee Leakage in Payment Settlements

Payment gateways settle transactions net of Merchant Discount Rates (MDR), taxes, and chargeback adjustments. Due to system discrepancies, billing misclassifications occur frequently (e.g., misclassifying a debit card as a corporate credit card). 

* **Revenue Leakage:** Industry data from EY indicates that businesses lose between 1% and 5% of annual revenue to billing and payment discrepancies.
* **The Scale Problem:** High-volume merchants processing 50,000+ transactions monthly cannot manually verify fee percentages and tax calculations at the transaction level, resulting in unrecovered overcharges.

---

## System Architecture and Pipeline Flow

WeldedDifff processes reconciliation via a single, automated matching pipeline:

```
[Raw CSV Inputs]
       │
       ▼
[Deterministic Matching Engine] ──(Matched 90%)──► [Settled Ledger (Auto-Commit)]
       │
  (Unresolved 10%)
       │
       ▼
[LLM Matching Resolution] (Direct API Call)
       │
       ▼
[Confidence Gate (Score >= 85)]
       ├── (Passes Gate) ──► [Proposed Matches (Pending Human Review)]
       └── (Fails Gate)  ──► [Abstained Exceptions (Requires Manual Audit)]
```

### The Three Matching Phases
1. **Deterministic Phase:** The core engine matches records using strict mathematical invariants. It groups split payments, accounts for timing delays (up to T+2 days), and matches bulk settlements. Matches resolved with 100% mathematical certainty are auto-committed.
2. **Probabilistic Phase:** Unresolved exceptions (such as fuzzy merchant descriptors or fee discrepancies) are extracted. The system queries the LLM (Gemini 2.5 Flash) via direct API calls to evaluate similarity and contextual data (e.g., customer communication history).
3. **Confidence Gate:** The LLM returns a structured JSON containing a confidence score and a traceable justification. A deterministic gate filters proposals. If the score is >= 85, the match is routed to the human-in-the-loop review queue. If below, the engine abstains.

---

## Evaluation: Naive Baseline vs. Advanced Pipeline

The engine evaluates performance against a naive exact-matching baseline:
* **Naive Baseline Matcher:** Restricts matching to exact matches on transaction IDs, UTRs, and matching amounts.
* **Equilibrium Advanced Engine:** Applies fuzzy logic, batch grouping, split payouts, and LLM matching.

### Planted Edge Case: Duplicate Transaction & Refund
To test pipeline boundaries, the dataset contains a duplicate payment scenario:
* A customer initiates two attempts for 1,500.00 INR. One succeeds (`ord_dup_1`), and one fails (`ord_dup_2`). The successful transaction is later refunded.
* The naive baseline fails to reconcile the refund debit and leaves `ord_dup_2` unresolved.
* WeldedDifff auto-resolves the failed order, matches the initial credit, and links the refund debit to the original payout using temporal analysis and UTR logging.

---

## Known Limitation
If the bank statement narrative is completely stripped of descriptive metadata (e.g., containing only a generic sequence number with no merchant or customer indicators) and does not map to a gateway-provided UTR, the engine cannot resolve the link. In these cases, the pipeline will always choose to abstain to prevent ledger corruption.

---

## Running the Project

### 1. Installation
Install dependencies via pip:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file in the root directory and add your Gemini API key:
```bash
echo "GEMINI_API_KEY=your_api_key_here" > .env
```

### 3. Generate Data
Generate the synthetic transaction datasets:
```bash
python -m src.generator
```

### 4. Run Reconciliation Pipeline
Execute the reconciliation pipeline:
```bash
python -m src.pipeline
```
This script outputs a summary JSON and generates traces in the `traces/` folder.

### 5. Launch the Review Dashboard
To run the Streamlit review dashboard:
```bash
streamlit run app.py
```

### 6. Verification & Determinism Checks
Run automated checks:
* **Unit Tests:** `pytest tests/`
* **Determinism Verification:** `python -m scripts.run_determinism_check`
