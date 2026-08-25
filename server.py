import os
import json
import csv
import io
import subprocess
import sys
from datetime import date, datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(BASE_DIR, "traces")
DATA_DIR = os.path.join(BASE_DIR, "data")
DECISION_LOG_PATH = os.path.join(TRACES_DIR, "decision_log.json")
SUMMARY_REPORT_PATH = os.path.join(TRACES_DIR, "summary_report.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="WeldedDiff Reconciliation Review API")

def load_json_file(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json_file(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ── Summary ──────────────────────────────────────────────────────────────────
@app.get("/api/summary")
def get_summary():
    data = load_json_file(SUMMARY_REPORT_PATH)
    if not data:
        raise HTTPException(status_code=404, detail="Summary report not found")
    return data

# ── Traces ────────────────────────────────────────────────────────────────────
@app.get("/api/traces")
def get_traces():
    data = load_json_file(DECISION_LOG_PATH)
    if not data:
        raise HTTPException(status_code=404, detail="Decision log not found")
    return data

@app.get("/api/traces/{ref_id}")
def get_trace_by_id(ref_id: str):
    traces = load_json_file(DECISION_LOG_PATH) or []
    matching = [t for t in traces if t.get("payment_id") == ref_id or t.get("order_id") == ref_id or t.get("utr") == ref_id]
    if not matching:
        raise HTTPException(status_code=404, detail=f"No trace found for {ref_id}")
    return matching

# ── HITL Approve / Reject ─────────────────────────────────────────────────────
@app.post("/api/traces/{ref_id}/approve")
def approve_trace(ref_id: str):
    traces = load_json_file(DECISION_LOG_PATH) or []
    updated = False
    for t in traces:
        if (t.get("payment_id") == ref_id or t.get("utr") == ref_id) and t.get("decision") in ("PROPOSED", "ABSTAINED"):
            t["decision"] = "USER_APPROVED"
            t["reason"] = f"Approved by financial controller — {t.get('llm_justification', 'manual override')}"
            updated = True
            break
    if updated:
        save_json_file(DECISION_LOG_PATH, traces)
        return {"status": "success", "message": f"Transaction {ref_id} approved", "new_decision": "USER_APPROVED"}
    raise HTTPException(status_code=400, detail=f"No actionable trace found for {ref_id}")

@app.post("/api/traces/{ref_id}/reject")
def reject_trace(ref_id: str):
    traces = load_json_file(DECISION_LOG_PATH) or []
    updated = False
    for t in traces:
        if (t.get("payment_id") == ref_id or t.get("utr") == ref_id) and t.get("decision") in ("PROPOSED", "USER_APPROVED"):
            t["decision"] = "ABSTAINED"
            t["reason"] = "Rejected by financial controller — overridden to exception queue"
            updated = True
            break
    if updated:
        save_json_file(DECISION_LOG_PATH, traces)
        return {"status": "success", "message": f"Transaction {ref_id} rejected", "new_decision": "ABSTAINED"}
    raise HTTPException(status_code=400, detail=f"No actionable trace found for {ref_id}")

# ── Download Sample CSV Templates ─────────────────────────────────────────────
@app.get("/api/download_template/{template_type}")
def download_template(template_type: str):
    output = io.StringIO()
    writer = csv.writer(output)
    today = date.today().strftime("%Y-%m-%d")

    if template_type.lower() in ("payouts", "razorpay"):
        writer.writerow(["payment_id", "order_id", "amount", "fee", "tax_gst", "settlement_id", "settled_at", "utr"])
        writer.writerow(["pay_sample_01", "ord_sample_01", "12500.00", "250.00", "45.00", "set_sample_101", f"{today} 10:30:00", "UTR_SMPL001"])
        writer.writerow(["pay_sample_02", "ord_sample_02", "8400.00", "168.00", "30.24", "set_sample_101", f"{today} 11:15:00", "UTR_SMPL002"])
        writer.writerow(["pay_sample_03", "ord_sample_03", "19999.00", "399.98", "72.00", "set_sample_102", f"{today} 14:00:00", "UTR_SMPL003"])
        filename = "WeldedDiff_Payouts_Template.csv"
    elif template_type.lower() in ("bank", "statement"):
        writer.writerow(["date", "description", "amount_credited", "amount_debited"])
        writer.writerow([today, "CMS/RAZORPAY PAYOUTS/set_sample_101/BATCH", "20406.76", "0.00"])
        writer.writerow([today, "CMS/RZRPY SETTLEMENT/set_sample_102/BATCH", "19527.02", "0.00"])
        writer.writerow([today, "BANK CHARGES / MONTHLY MAINTENANCE", "0.00", "150.00"])
        filename = "WeldedDiff_BankStatement_Template.csv"
    else:
        raise HTTPException(status_code=400, detail="Invalid template type. Use 'payouts' or 'bank'.")

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ── CSV / ERP Format Export ───────────────────────────────────────────────────
@app.get("/api/export")
def export_reconciliation_report(format: str = Query("standard", regex="^(standard|tally|zoho)$")):
    traces = load_json_file(DECISION_LOG_PATH) or []
    summary = load_json_file(SUMMARY_REPORT_PATH) or {}

    # De-duplicate: last trace per ref_id
    seen = {}
    for t in traces:
        key = t.get("payment_id") or t.get("order_id") or t.get("utr")
        if key:
            seen[key] = t

    output = io.StringIO()
    writer = csv.writer(output)
    today = date.today().strftime("%Y-%m-%d")

    if format == "tally":
        # Tally ERP format
        writer.writerow([
            "Voucher_Date", "Voucher_Type", "Voucher_No",
            "Ledger_Name", "Debit_Amount", "Credit_Amount",
            "Narration", "Recon_Status"
        ])
        for t in seen.values():
            ref = t.get("payment_id") or t.get("order_id") or t.get("utr") or "—"
            amt = t.get("net_calculated") or t.get("amount") or "0.00"
            writer.writerow([
                today,
                "Bank Receipt",
                ref,
                "Razorpay Clearing Account",
                amt if t.get("decision") in ("AUTO_COMMIT", "USER_APPROVED") else "0.00",
                "0.00",
                t.get("reason") or "Reconciled via WeldedDiff",
                t.get("decision") or "—"
            ])
        filename = f"WeldedDiff_Tally_ERP_{today}.csv"

    elif format == "zoho":
        # Zoho Books Bank Feed layout
        writer.writerow([
            "Date", "Description", "Reference_Number",
            "Withdrawals", "Deposits", "Reconciliation_Status", "Matched_UTR"
        ])
        for t in seen.values():
            ref = t.get("payment_id") or t.get("order_id") or t.get("utr") or "—"
            amt = t.get("net_calculated") or t.get("amount") or "0.00"
            writer.writerow([
                today,
                t.get("reason") or "WeldedDiff 3-Way Match",
                ref,
                "0.00",
                amt if t.get("decision") in ("AUTO_COMMIT", "USER_APPROVED") else "0.00",
                t.get("decision") or "—",
                t.get("utr") or "—"
            ])
        filename = f"WeldedDiff_ZohoBooks_{today}.csv"

    else:
        # Standard format
        writer.writerow([
            "Transaction_ID", "Matched_UTR", "Step",
            "Gross_Amount_INR", "Net_Amount_INR",
            "Reconciliation_Status", "Matching_Source", "Audit_Reason"
        ])
        for t in seen.values():
            ref = t.get("payment_id") or t.get("order_id") or t.get("utr") or "—"
            src = "Deterministic" if t.get("decision") == "AUTO_COMMIT" else "Human Override" if t.get("decision") == "USER_APPROVED" else "LLM Audit"
            writer.writerow([
                ref,
                t.get("utr") or "—",
                t.get("step") or "—",
                t.get("amount") or t.get("amount_debited") or "—",
                t.get("net_calculated") or "—",
                t.get("decision") or "—",
                src,
                t.get("reason") or "—"
            ])
        filename = f"WeldedDiff_Reconciliation_{today}.csv"

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ── Run pipeline with validation & live execution capture ─────────────────────
@app.post("/api/run_pipeline")
async def run_pipeline(
    orders_file: UploadFile = File(None),
    bank_file: UploadFile = File(None)
):
    """
    Validates CSV uploads, updates data directory, runs pipeline, and returns logs + summary.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # 1. Validate payouts file if uploaded
    if orders_file and orders_file.filename:
        content = await orders_file.read()
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header:
            raise HTTPException(status_code=400, detail="Payouts CSV file is empty")
        header_lower = [h.strip().lower() for h in header]
        
        # Check required columns
        if not ("payment_id" in header_lower or "order_id" in header_lower):
            raise HTTPException(
                status_code=400,
                detail="Payouts CSV validation failed: Missing required identifier column ('payment_id' or 'order_id')"
            )
        if not ("amount" in header_lower or "amount_debited" in header_lower):
            raise HTTPException(
                status_code=400,
                detail="Payouts CSV validation failed: Missing required column 'amount'"
            )
        
        # Write validated file
        target_name = "bank_statements.csv" if ("bank" in orders_file.filename.lower() or "statement" in orders_file.filename.lower()) else "razorpay_payouts.csv"
        with open(os.path.join(DATA_DIR, target_name), "wb") as f:
            f.write(content)

    # 2. Validate bank file if uploaded
    if bank_file and bank_file.filename:
        content = await bank_file.read()
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header:
            raise HTTPException(status_code=400, detail="Bank Statement CSV file is empty")
        header_lower = [h.strip().lower() for h in header]

        if not ("description" in header_lower or "narration" in header_lower):
            raise HTTPException(
                status_code=400,
                detail="Bank statement CSV validation failed: Missing required 'description' / 'narration' column"
            )

        with open(os.path.join(DATA_DIR, "bank_statements.csv"), "wb") as f:
            f.write(content)

    # 3. Execute pipeline process
    process = subprocess.run(
        [sys.executable, "-m", "src.pipeline"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120
    )

    log_lines = []
    if process.stdout:
        log_lines.extend([line for line in process.stdout.splitlines() if line.strip()])
    if process.stderr:
        log_lines.extend([f"[stderr] {line}" for line in process.stderr.splitlines() if line.strip()])

    if process.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Reconciliation engine failed: {process.stderr[:400]}"
        )

    summary = load_json_file(SUMMARY_REPORT_PATH) or {}
    traces = load_json_file(DECISION_LOG_PATH) or []
    return {
        "status": "success",
        "summary": summary,
        "trace_count": len(traces),
        "logs": log_lines
    }

# ── Static & root ──────────────────────────────────────────────────────────────
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def read_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"message": "Frontend not found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
