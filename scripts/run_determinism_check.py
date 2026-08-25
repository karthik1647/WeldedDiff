import os
import json
import sys
from src.pipeline import ReconciliationPipeline

def run_determinism_test():
    print("Starting Determinism Verification Check...")
    
    # Check if GEMINI_API_KEY is available (if not, we skip LLM variations)
    api_key = os.getenv("GEMINI_API_KEY")
    pipeline = ReconciliationPipeline(api_key=api_key)
    
    # Run 1
    print("Executing Run 1...")
    summary1, proposed1, exceptions1 = pipeline.run()
    
    # Load traces from Run 1
    traces_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "traces", "decision_log.json")
    with open(traces_path, "r", encoding="utf-8") as f:
        traces1 = json.load(f)
        
    # Run 2
    print("Executing Run 2...")
    summary2, proposed2, exceptions2 = pipeline.run()
    
    # Load traces from Run 2
    with open(traces_path, "r", encoding="utf-8") as f:
        traces2 = json.load(f)

    # 1. Assert size match
    if len(traces1) != len(traces2):
        print(f"FAILED: Trace log length mismatch: Run 1={len(traces1)} vs Run 2={len(traces2)}")
        sys.exit(1)
        
    # 2. Compare matching decisions
    deterministic_mismatches = []
    llm_mismatches = []
    
    for t1, t2 in zip(traces1, traces2):
        # Validate that the same step and entities are compared
        if t1.get("step") != t2.get("step") or t1.get("payment_id") != t2.get("payment_id") or t1.get("order_id") != t2.get("order_id"):
            print(f"FAILED: Out of order execution logs detected: {t1} vs {t2}")
            sys.exit(1)
            
        step_type = t1.get("step")
        decision1 = t1.get("decision")
        decision2 = t2.get("decision")
        
        if decision1 != decision2:
            mismatch_info = {
                "step": step_type,
                "payment_id": t1.get("payment_id"),
                "order_id": t1.get("order_id"),
                "utr": t1.get("utr"),
                "run1_decision": decision1,
                "run2_decision": decision2
            }
            if "llm" in step_type:
                llm_mismatches.append(mismatch_info)
            else:
                deterministic_mismatches.append(mismatch_info)

    # 3. Report Results
    print("\n--- Verification Report ---")
    print(f"Total Transactions Audited: {len(traces1)}")
    
    # Invariant: Deterministic matching must be 100% stable
    if len(deterministic_mismatches) > 0:
        print(f"CRITICAL FAILURE: Deterministic matching is non-deterministic. {len(deterministic_mismatches)} mismatches found.")
        for item in deterministic_mismatches:
            print(f"  - Step: {item['step']}, Payment: {item['payment_id']}, Order: {item['order_id']} | Run 1: {item['run1_decision']} vs Run 2: {item['run2_decision']}")
        sys.exit(1)
    else:
        print("PASS: Deterministic Matching is 100% stable and consistent.")

    # LLM matching may fluctuate due to model non-determinism
    if len(llm_mismatches) > 0:
        print(f"NOTICE: LLM resolution phase has {len(llm_mismatches)} decision fluctuations between runs due to API temperature variance.")
        for item in llm_mismatches:
            print(f"  - Step: {item['step']}, Payment: {item['payment_id']} | Run 1: {item['run1_decision']} vs Run 2: {item['run2_decision']}")
    else:
        print("PASS: LLM matching resolved identically across both runs.")

    print("\nDeterminism check execution finished.")
    sys.exit(0)

if __name__ == "__main__":
    run_determinism_test()
