import os
import json
import pandas as pd
import streamlit as st
from decimal import Decimal

# Setup directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TRACES_DIR = os.path.join(BASE_DIR, "traces")
DECISION_LOG_PATH = os.path.join(TRACES_DIR, "decision_log.json")
SUMMARY_REPORT_PATH = os.path.join(TRACES_DIR, "summary_report.json")

st.set_page_config(page_title="WeldedDiff Reconciliation Review", layout="wide")

# Inject light custom styling to avoid default stock template look
st.markdown("""
<style>
    /* Metric Card Styling */
    [data-testid="stMetricValue"] {
        font-size: 2rem !important;
        font-weight: 700;
        color: #1e293b;
    }
    div[data-testid="stMetric"] {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        padding: 1.25rem;
        border-radius: 8px;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
    }
    /* Tab Styling */
    button[data-baseweb="tab"] {
        font-weight: 600;
        color: #64748b;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #0f172a;
        border-bottom-color: #0f172a;
    }
    /* Button Customizations */
    .stButton>button {
        border-radius: 6px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# Helper to load JSON files safely
def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Helper to save traces data back to file
def save_traces(traces_data):
    with open(DECISION_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(traces_data, f, indent=2)

# Load data
summary = load_json(SUMMARY_REPORT_PATH)
traces = load_json(DECISION_LOG_PATH)

st.title("WeldedDiff Reconciliation Review")

if not summary or not traces:
    st.warning("Reconciliation data not found. Please run the pipeline script first to generate reports:")
    st.code("python -m src.pipeline")
else:
    # Compile dynamic stats based on current state of decision_log.json
    total_orders = summary.get("baseline", {}).get("baseline_matched_orders", 0)
    llm_calls_made = summary.get("llm_calls_made", 0)
    
    proposed_traces = [t for t in traces if t.get("decision") == "PROPOSED"]
    abstained_traces = [t for t in traces if t.get("decision") == "ABSTAINED"]
    auto_committed_traces = [t for t in traces if t.get("decision") in ("AUTO_COMMIT", "USER_APPROVED")]
    
    total_payouts = summary.get("baseline", {}).get("baseline_matched_orders", 0) # total payouts proxy from baseline matches
    total_bank_credits = summary.get("baseline", {}).get("baseline_matched_bank_credits", 0)
    
    # Recalculate match rate dynamically based on current commits
    committed_count = len([t for t in traces if t.get("decision") in ("AUTO_COMMIT", "USER_APPROVED") and t.get("step") in ("payout_to_bank_utr", "payout_to_bank_settlement_batch")])
    total_bank_credits_count = summary.get("baseline", {}).get("baseline_matched_bank_credits", 0) + summary.get("llm_abstained_payouts", 0)
    
    # 1. KPI Panel
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="Baseline Match Rate (Exact Matches)",
            value=f"{summary.get('baseline', {}).get('baseline_bank_match_rate_pct', 0.0)}%",
            delta=None
        )
        
    with col2:
        # Calculate dynamic advanced match rate
        # baseline rate is bank credits matched exactly. Advanced match includes deterministic advanced + user approved payouts.
        denom = summary.get("baseline", {}).get("baseline_matched_bank_credits", 0) + len(proposed_traces) + len([t for t in traces if t.get("step") == "llm_payout_audit" and t.get("decision") == "ABSTAINED"])
        numer = summary.get("deterministic_reconciled_payouts", 0) + len([t for t in traces if t.get("decision") == "USER_APPROVED" and t.get("step") == "llm_payout_audit"])
        advanced_rate = (numer / denom) * 100 if denom > 0 else 0.0
        
        baseline_rate = summary.get("baseline", {}).get("baseline_bank_match_rate_pct", 0.0)
        delta_val = f"+{round(advanced_rate - float(baseline_rate), 2)}%"
        st.metric(
            label="Pipeline Match Rate (With LLM Review)",
            value=f"{round(advanced_rate, 2)}%",
            delta=delta_val
        )
        
    with col3:
        st.metric(
            label="Total Transactions Reviewed",
            value=str(total_orders + llm_calls_made)
        )
        
    with col4:
        st.metric(
            label="LLM Execution Cost",
            value=f"${summary.get('llm_processing_cost_usd', 0.00):.4f}",
            delta=f"{llm_calls_made} API Calls"
        )

    # 2. Performance Comparison Chart
    st.write("---")
    chart_data = pd.DataFrame({
        "Metric": ["Exact Baseline Match", "Pipeline Match (With LLM Review)"],
        "Payout Match Rate (%)": [
            float(summary.get("baseline", {}).get("baseline_bank_match_rate_pct", 0.0)),
            float(round(advanced_rate, 2))
        ]
    })
    st.bar_chart(chart_data, x="Metric", y="Payout Match Rate (%)")

    # 3. Interactive Review Desk (Human-In-The-Loop Approval)
    st.write("---")
    
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
                ref_id = trace.get('payment_id') or trace.get('utr')
                with st.expander(f"Proposed Match - Reference ID: {ref_id} (Confidence: {trace.get('llm_confidence')}%)"):
                    st.write(f"**Step:** {trace.get('step')}")
                    st.write(f"**Justification Trace:** {trace.get('llm_justification')}")
                    st.write(f"**Decision Reasoning:** {trace.get('reason')}")
                    
                    # Layout buttons inside expander
                    col_b1, col_b2 = st.columns(8)
                    
                    with col_b1:
                        # On click, mutate the matching trace in our json list
                        if st.button("Approve Match", key=f"app_{ref_id}_{idx}"):
                            for t in traces:
                                if t.get("payment_id") == trace.get("payment_id") and t.get("step") == trace.get("step"):
                                    t["decision"] = "USER_APPROVED"
                                    t["reason"] = f"User Approved: {proposal_justification if (proposal_justification := trace.get('llm_justification')) else 'Approved manually'}"
                                    break
                            save_traces(traces)
                            st.success(f"Match Approved for {ref_id}.")
                            st.rerun()
                            
                    with col_b2:
                        # On click, set decision to ABSTAINED to move out of Proposed tab
                        if st.button("Reject & Abstain", key=f"rej_{ref_id}_{idx}"):
                            for t in traces:
                                if t.get("payment_id") == trace.get("payment_id") and t.get("step") == trace.get("step"):
                                    t["decision"] = "ABSTAINED"
                                    t["reason"] = "User Rejected: Overridden to Abstained Exception"
                                    break
                            save_traces(traces)
                            st.warning(f"Match Rejected for {ref_id}.")
                            st.rerun()

    with tab2:
        st.write("The following transactions could not be resolved by the engine or LLM (fell below the confidence gate). They require manual operational review.")
        
        if not abstained_traces:
            st.success("No open exceptions.")
        else:
            exc_data = []
            for trace in abstained_traces:
                exc_data.append({
                    "Step": trace.get("step"),
                    "Payment ID/Ref": trace.get("payment_id") or trace.get("utr") or "N/A",
                    "Expected Value": trace.get("net_calculated") or trace.get("amount_debited") or "N/A",
                    "Reason for Abstaining": trace.get("reason"),
                    "LLM Confidence Score": trace.get("llm_confidence", "N/A")
                })
            st.table(pd.DataFrame(exc_data))

    with tab3:
        st.write("These records were matched by the deterministic engine or approved by the user and committed to the database.")
        
        if not auto_committed_traces:
            st.info("No committed records.")
        else:
            commit_data = []
            for trace in auto_committed_traces:
                commit_data.append({
                    "Step": trace.get("step"),
                    "Order ID/Ref": trace.get("order_id") or trace.get("payment_id") or "N/A",
                    "Amount": trace.get("amount") or trace.get("net_calculated") or trace.get("amount_debited") or "N/A",
                    "Resolution Detail": trace.get("reason")
                })
            st.dataframe(pd.DataFrame(commit_data), use_container_width=True)
