import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(BASE_DIR, "traces")
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

# API Endpoints
@app.get("/api/summary")
def get_summary():
    data = load_json_file(SUMMARY_REPORT_PATH)
    if not data:
        raise HTTPException(status_code=404, detail="Summary report not found")
    return data

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
        raise HTTPException(status_code=404, detail=f"No trace found for transaction ID {ref_id}")
    return matching

@app.post("/api/traces/{ref_id}/approve")
def approve_trace(ref_id: str):
    traces = load_json_file(DECISION_LOG_PATH) or []
    updated = False
    for t in traces:
        if (t.get("payment_id") == ref_id or t.get("utr") == ref_id) and t.get("decision") == "PROPOSED":
            t["decision"] = "USER_APPROVED"
            t["reason"] = f"User Approved: {t.get('llm_justification', 'Approved manually')}"
            updated = True
            break
    if updated:
        save_json_file(DECISION_LOG_PATH, traces)
        return {"status": "success", "message": f"Transaction {ref_id} approved"}
    raise HTTPException(status_code=400, detail=f"No pending proposed trace found for {ref_id}")

@app.post("/api/traces/{ref_id}/reject")
def reject_trace(ref_id: str):
    traces = load_json_file(DECISION_LOG_PATH) or []
    updated = False
    for t in traces:
        if (t.get("payment_id") == ref_id or t.get("utr") == ref_id) and t.get("decision") == "PROPOSED":
            t["decision"] = "ABSTAINED"
            t["reason"] = "User Rejected: Overridden to Abstained Exception"
            updated = True
            break
    if updated:
        save_json_file(DECISION_LOG_PATH, traces)
        return {"status": "success", "message": f"Transaction {ref_id} rejected and abstained"}
    raise HTTPException(status_code=400, detail=f"No pending proposed trace found for {ref_id}")

# Mount static folder
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def read_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"message": "Frontend index.html not found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
