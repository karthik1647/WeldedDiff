import os
import json
import csv
import io
import subprocess
import sys
from datetime import date
from fastapi import FastAPI, HTTPException, UploadFile, File
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

# ── CSV Export ────────────────────────────────────────────────────────────────
@app.get("/api/export")
def export_reconciliation_report():
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

    today = date.today().strftime("%Y-%m-%d")
    filename = f"WeldedDiff_Reconciliation_{today}.csv"
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ── Run pipeline with custom uploaded files ────────────────────────────────────
@app.post("/api/run_pipeline")
async def run_pipeline(
    orders_file: UploadFile = File(None),
    bank_file: UploadFile = File(None)
):
    """
    Accepts optional CSV uploads. If provided, writes them to data/.
    Then re-runs src/pipeline.py and returns fresh summary + traces.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    if orders_file and orders_file.filename:
        content = await orders_file.read()
        # Detect which file type by sniffing header
        header = content.decode("utf-8", errors="replace").split("\n")[0]
        if "bank" in orders_file.filename.lower() or "statement" in orders_file.filename.lower():
            with open(os.path.join(DATA_DIR, "bank_statements.csv"), "wb") as f:
                f.write(content)
        else:
            with open(os.path.join(DATA_DIR, "razorpay_payouts.csv"), "wb") as f:
                f.write(content)

    if bank_file and bank_file.filename:
        content = await bank_file.read()
        with open(os.path.join(DATA_DIR, "bank_statements.csv"), "wb") as f:
            f.write(content)

    # Re-run pipeline
    result = subprocess.run(
        [sys.executable, "-m", "src.pipeline"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120
    )

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {result.stderr[:500]}")

    summary = load_json_file(SUMMARY_REPORT_PATH) or {}
    traces = load_json_file(DECISION_LOG_PATH) or []
    return {"status": "success", "summary": summary, "trace_count": len(traces)}

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
