# Incident Postmortem: Case 01 Duplicate Payment Collision

## Incident Summary
During transaction reconciliation for the date range 2026-08-25 to 2026-08-30, a ledger imbalance occurred due to duplicate transaction submissions mapping to a single bank statement credit. 

* **Impact:** 1 order remained unreconciled; 1 bank debit of 1,500.00 INR was unmapped.
* **Leakage Value:** 1,500.00 INR.

---

## Technical Details

A customer initiated two separate payment attempts of exactly 1,500.00 INR within a 5-minute window:
1. **Transaction 1 (Captured):** `ord_dup_1` / `pay_dup_1` occurred at 14:00:00 (Amount: 1,500.00 INR).
2. **Transaction 2 (Failed):** `ord_dup_2` at 14:05:00 (Amount: 1,500.00 INR).

The bank statement subsequently logged two entries:
1. **Credit Entry (Settled):** A credit of 1,464.60 INR on 2026-08-29 matching UTR `UTR_DUP_SUCCESS` (net of 30.00 INR fee and 5.40 INR GST).
2. **Debit Entry (Refund):** A debit of 1,500.00 INR on 2026-08-30 containing reference `UTR_DUP_SUCCESS` in the description narration.

### Why the Naive Baseline Failed
The naive baseline matcher matched the bank statement credit to `ord_dup_1` and `pay_dup_1` because both amounts and UTR references matched. However, the naive baseline could not resolve:
* The refund debit of 1,500.00 INR, because it exact-matches credits only.
* The failed order `ord_dup_2` of 1,500.00 INR, which remained open and flagged as a transaction variance.

### How Equilibrium Resolved the Case
1. **Deterministic Phase:** 
   * Auto-resolved `ord_dup_2` as "resolved_no_payout" because its status in the internal database was marked as `failed`, ensuring it did not require a matching payout record.
   * Auto-resolved the credit of 1,464.60 INR to `pay_dup_1` based on the exact UTR match `UTR_DUP_SUCCESS` and correct net payout calculations.
2. **LLM Forensic Audit Phase:**
   * The remaining bank statement debit of 1,500.00 INR was analyzed by the LLM auditor against payout candidates.
   * Using temporal logic (the refund occurred on August 30, which is after the August 29 settlement of `pay_dup_1`) and matching the reference `UTR_DUP_SUCCESS` in the bank debit description, the LLM proposed a match mapping the debit back to `pay_dup_1` as a verified refund with a confidence score of 95.
   * The proposed match was logged for human review, reducing the open variance to 0.00 INR.

---

## Action and Prevention Items
1. **Ledger Schema Enforcement:** Update database schemas to store a unique transaction reference token for all manual refund intents.
2. **Idempotency keys:** Ensure the checkout portal generates client-side idempotency keys to prevent double-submit attempts within a 15-minute window for identical cart values.
