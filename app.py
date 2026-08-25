import os
import json
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from decimal import Decimal

# Setup directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TRACES_DIR = os.path.join(BASE_DIR, "traces")
DECISION_LOG_PATH = os.path.join(TRACES_DIR, "decision_log.json")
SUMMARY_REPORT_PATH = os.path.join(TRACES_DIR, "summary_report.json")

st.set_page_config(page_title="WeldedDiff Reconciliation Review", layout="wide")

# Inject custom styling for visual hierarchy and Slate Teal corporate aesthetic
st.markdown("""
<style>
    /* Metric Card Container */
    div[data-testid="stMetric"] {
        background-color: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-left: 4px solid #0f766e !important;
        padding: 1.25rem !important;
        border-radius: 6px !important;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05) !important;
    }
    /* Muted, uppercase metric labels to enforce hierarchy */
    div[data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
        font-weight: 600 !important;
        color: #475569 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
    }
    /* Large bold numeric values */
    [data-testid="stMetricValue"] {
        font-size: 2.25rem !important;
        font-weight: 800 !important;
        color: #0f172a !important;
        margin-top: 4px !important;
    }
    /* Tab Navigation with Slate Teal Accent */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        color: #64748b !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #0f766e !important;
        border-bottom-color: #0f766e !important;
    }
    /* Interactive Buttons */
    .stButton>button {
        border-radius: 6px !important;
        font-weight: 600 !important;
        border-color: #cbd5e1 !important;
    }
    .stButton>button:hover {
        color: #0f766e !important;
        border-color: #0f766e !important;
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
    
    # Load payouts to get exact total count (denominator)
    df_payouts = pd.read_csv(os.path.join(DATA_DIR, "razorpay_payouts.csv"))
    total_payout_count = len(df_payouts) # Denominator: 102 payouts
    
    # Baseline matched payouts (exact matching)
    # The baseline matched 2 bank credits, which represents 2 payouts matched out of 102 payouts.
    baseline_matched_payouts = summary.get("baseline", {}).get("baseline_matched_bank_credits", 0)
    baseline_payout_rate = (baseline_matched_payouts / total_payout_count) * 100 if total_payout_count > 0 else 0.0
    
    # Advanced matched payouts (deterministic matches + user approved matches)
    user_approved_payouts = len([t for t in traces if t.get("decision") == "USER_APPROVED" and t.get("step") == "llm_payout_audit"])
    advanced_matched_payouts = summary.get("deterministic_reconciled_payouts", 0) + user_approved_payouts
    advanced_payout_rate = (advanced_matched_payouts / total_payout_count) * 100 if total_payout_count > 0 else 0.0
    
    # 1. KPI Panel
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="Baseline Match Rate (Exact Matches)",
            value=f"{round(baseline_payout_rate, 2)}%",
            help=f"Exact matches: {baseline_matched_payouts} / {total_payout_count} payouts",
            delta=None
        )
        
    with col2:
        delta_val = f"+{round(advanced_payout_rate - baseline_payout_rate, 2)}%"
        st.metric(
            label="Pipeline Match Rate (With LLM Review)",
            value=f"{round(advanced_payout_rate, 2)}%",
            help=f"Reconciled payouts (Deterministic + LLM): {advanced_matched_payouts} / {total_payout_count} payouts",
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
    st.write("### Match Rate Comparison")
    
    fig, ax = plt.subplots(figsize=(8, 2.2))
    categories = ["Exact Baseline Match", "Pipeline Match\n(With LLM Review)"]
    rates = [float(baseline_payout_rate), float(advanced_payout_rate)]
    colors = ["#cbd5e1", "#0f766e"] # Slate gray for baseline, Slate Teal for advanced
    
    bars = ax.barh(categories, rates, color=colors, height=0.45)
    ax.set_xlim(0, 110)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.tick_params(axis="both", colors="#475569", labelsize=9)
    ax.xaxis.grid(True, linestyle="--", alpha=0.3, color="#cbd5e1")
    ax.set_axisbelow(True)
    
    # Label tip numbers
    for bar in bars:
        width = bar.get_width()
        ax.text(
            width + 1.5,
            bar.get_y() + bar.get_height() / 2,
            f"{width:.2f}%",
            ha="left",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="#1e293b"
        )
        
    st.pyplot(fig)

    # 3. Interactive Review Desk (Human-In-The-Loop Approval)
    st.write("---")
    
    tab1, tab2, tab3 = st.tabs([
        f"Proposed Matches ({len(proposed_traces)})",
        f"Abstained Exceptions ({len(abstained_traces)})",
        f"Committed Matches ({len(auto_committed_traces)})"
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
                provenance = "Deterministic (100% Certain)" if trace.get("decision") == "AUTO_COMMIT" else "Human-Approved (LLM Proposal)"
                commit_data.append({
                    "Step": trace.get("step"),
                    "Order ID/Ref": trace.get("order_id") or trace.get("payment_id") or "N/A",
                    "Amount": trace.get("amount") or trace.get("net_calculated") or trace.get("amount_debited") or "N/A",
                    "Matching Source": provenance,
                    "Resolution Detail": trace.get("reason")
                })
            st.dataframe(pd.DataFrame(commit_data), use_container_width=True)
