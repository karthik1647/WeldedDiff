import os
import json
import pandas as pd
import streamlit as st
from decimal import Decimal

# Setup directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TRACES_DIR = os.path.join(BASE_DIR, "traces")

st.set_page_config(page_title="WeldedDifff Reconciliation Desk", layout="wide")

# Helper to load JSON files safely
def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Load data
summary = load_json(os.path.join(TRACES_DIR, "summary_report.json"))
traces = load_json(os.path.join(TRACES_DIR, "decision_log.json"))

st.title("WeldedDifff: Multi-Gateway Ledger Reconciliation & Forensic Audit Desk")
st.subheader("Financial Operations Control Center")

if not summary or not traces:
    st.warning("Reconciliation data not found. Please run the pipeline script first to generate reports:")
    st.code("python -m src.pipeline")
else:
    # 1. KPI Panel
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="Baseline Match Rate (Exact Matches)",
            value=f"{summary.get('baseline', {}).get('baseline_bank_match_rate_pct', 0.0)}%",
            delta=None
        )
        
    with col2:
        match_rate = summary.get("advanced_payout_match_rate_pct", 0.0)
        baseline_rate = summary.get("baseline", {}).get("baseline_bank_match_rate_pct", 0.0)
        delta_val = f"+{round(match_rate - baseline_rate, 2)}%"
        st.metric(
            label="Equilibrium Match Rate (Advanced Pipeline)",
            value=f"{match_rate}%",
            delta=delta_val
        )
        
    with col3:
        st.metric(
            label="Total Audited GMV Transactions",
            value=f"{summary.get('baseline', {}).get('baseline_matched_orders', 0) + summary.get('llm_calls_made', 0)}"
        )
        
    with col4:
        st.metric(
            label="LLM Execution Cost",
            value=f"${summary.get('llm_processing_cost_usd', 0.00):.4f}",
            delta=f"{summary.get('llm_calls_made', 0)} API Calls"
        )

    # 2. Performance Comparison chart
    st.write("---")
    st.header("Performance Comparison")
    chart_data = pd.DataFrame({
        "Metric": ["Exact Baseline Match", "Equilibrium Advanced Match"],
        "Payout Match Rate (%)": [
            summary.get("baseline", {}).get("baseline_bank_match_rate_pct", 0.0),
            summary.get("advanced_payout_match_rate_pct", 0.0)
        ]
    })
    st.bar_chart(chart_data, x="Metric", y="Payout Match Rate (%)")

    # 3. Interactive Review Desk (Human-In-The-Loop Approval)
    st.write("---")
    st.header("Audit Review Desk")
    
    # Filter traces for Proposed Matches
    proposed_traces = [t for t in traces if t.get("decision") == "PROPOSED"]
    abstained_traces = [t for t in traces if t.get("decision") == "ABSTAINED"]
    auto_committed_traces = [t for t in traces if t.get("decision") == "AUTO_COMMIT"]

    tab1, tab2, tab3 = st.tabs([
        f"Proposed Matches ({len(proposed_traces)})",
        f"Abstained Exceptions ({len(abstained_traces)})",
        f"Auto-Committed ({len(auto_committed_traces)})"
    ])

    with tab1:
        st.write("The following matches were resolved using probabilistic LLM auditing. Verify the justification and approve them to commit to the final ledger.")
        
        if not proposed_traces:
            st.success("No pending proposed matches.")
        else:
            for idx, trace in enumerate(proposed_traces):
                with st.expander(f"Proposed Match - Reference ID: {trace.get('payment_id') or trace.get('utr')} (Confidence: {trace.get('llm_confidence')}%)"):
                    st.write(f"**Step:** {trace.get('step')}")
                    st.write(f"**Justification Trace:** {trace.get('llm_justification')}")
                    st.write(f"**Decision Reasoning:** {trace.get('reason')}")
                    
                    # Simulated approval action
                    col_b1, col_b2 = st.columns(2)
                    with col_b1:
                        if st.button("Approve Match", key=f"app_{idx}"):
                            st.success(f"Match Approved and committed to Ledger for payment: {trace.get('payment_id')}")
                    with col_b2:
                        if st.button("Reject & Abstain", key=f"rej_{idx}"):
                            st.warning("Match rejected. Moved to exceptions.")

    with tab2:
        st.write("The following transactions could not be resolved by the engine or LLM (fell below the confidence gate). They require manual operational review.")
        
        if not abstained_traces:
            st.success("No open exceptions.")
        else:
            # Build exception table
            exc_data = []
            for trace in abstained_traces:
                exc_data.append({
                    "Step": trace.get("step"),
                    "Payment ID/Ref": trace.get("payment_id") or trace.get("utr"),
                    "Expected Value": trace.get("net_calculated") or trace.get("amount_debited"),
                    "Reason for Abstaining": trace.get("reason"),
                    "LLM Confidence Score": trace.get("llm_confidence", "N/A")
                })
            st.table(pd.DataFrame(exc_data))

    with tab3:
        st.write("These records were matched with 100% mathematical certainty by the deterministic engine and committed to the database.")
        
        if not auto_committed_traces:
            st.info("No auto-committed records.")
        else:
            commit_data = []
            for trace in auto_committed_traces:
                commit_data.append({
                    "Step": trace.get("step"),
                    "Order ID/Ref": trace.get("order_id") or trace.get("payment_id") or trace.get("bank_date"),
                    "Amount": trace.get("amount") or trace.get("net_calculated") or trace.get("amount_debited"),
                    "Resolution Detail": trace.get("reason")
                })
            st.dataframe(pd.DataFrame(commit_data), use_container_width=True)
