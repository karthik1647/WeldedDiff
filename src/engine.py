import os
import pandas as pd
from decimal import Decimal
from src.utils import (
    to_decimal, parse_date, normalize_text, extract_utr,
    extract_settlement_id, get_instrument_contract_rates,
    is_within_banking_sla, count_banking_business_days
)

class ReconciliationEngine:
    def __init__(self, orders_path, payouts_path, bank_path, apply_tds=False):
        self.orders_path = orders_path
        self.payouts_path = payouts_path
        self.bank_path = bank_path
        self.apply_tds = apply_tds
        
        # Load raw dataframes
        self.df_orders = pd.read_csv(orders_path)
        self.df_payouts = pd.read_csv(payouts_path)
        self.df_bank = pd.read_csv(bank_path)

        # Basic cleanup: convert decimals
        self.df_orders["amount"] = self.df_orders["amount"].apply(to_decimal)
        
        self.df_payouts["amount"] = self.df_payouts["amount"].apply(to_decimal)
        self.df_payouts["fee"] = self.df_payouts["fee"].apply(to_decimal)
        self.df_payouts["tax_gst"] = self.df_payouts["tax_gst"].apply(to_decimal)
        
        # Ensure payment_method is present (default to credit_card if legacy CSV)
        if "payment_method" not in self.df_payouts.columns:
            self.df_payouts["payment_method"] = "credit_card"
        else:
            self.df_payouts["payment_method"] = self.df_payouts["payment_method"].fillna("credit_card")
        
        # Calculate dynamic contract fees per payment instrument
        def calc_contract(row):
            method = row.get("payment_method", "credit_card")
            amt = row["amount"]
            rates = get_instrument_contract_rates(method)
            c_fee = to_decimal(amt * rates["mdr"])
            c_tax = to_decimal(c_fee * rates["gst"])
            tds = to_decimal(amt * Decimal("0.001")) if self.apply_tds else Decimal("0.00")
            c_net = amt - c_fee - c_tax - tds
            return pd.Series([c_fee, c_tax, tds, c_net])

        contract_vals = self.df_payouts.apply(calc_contract, axis=1)
        self.df_payouts["contract_fee"] = contract_vals[0]
        self.df_payouts["contract_tax"] = contract_vals[1]
        self.df_payouts["tds_amount"] = contract_vals[2]
        self.df_payouts["contract_net"] = contract_vals[3]
        
        self.df_payouts["net_calculated"] = self.df_payouts.apply(
            lambda r: r["amount"] - r["fee"] - r["tax_gst"] - (to_decimal(r["amount"] * Decimal("0.001")) if self.apply_tds else Decimal("0.00")),
            axis=1
        )
        
        self.df_bank["amount_credited"] = self.df_bank["amount_credited"].apply(to_decimal)
        self.df_bank["amount_debited"] = self.df_bank["amount_debited"].apply(to_decimal)
        
    def run_naive_baseline(self):
        """
        Runs a strict exact-matching baseline (Exact order_id matching and exact UTR/amount matching).
        """
        matched_orders = 0
        matched_payouts_to_bank = 0
        total_orders = len(self.df_orders)
        total_bank_credits = len(self.df_bank[self.df_bank["amount_credited"] > 0])
        
        # 1. Match Orders to Payouts
        payout_map = {row["order_id"]: row for _, row in self.df_payouts.iterrows()}
        for _, order in self.df_orders.iterrows():
            ord_id = order["order_id"]
            if ord_id in payout_map:
                payout = payout_map[ord_id]
                if order["amount"] == payout["amount"] and order["status"] == "captured":
                    matched_orders += 1
                    
        # 2. Match Payouts to Bank Credits
        bank_credits = self.df_bank[self.df_bank["amount_credited"] > 0]
        bank_utr_map = {}
        for _, row in bank_credits.iterrows():
            utr = extract_utr(row["description"])
            if utr:
                bank_utr_map[utr] = row["amount_credited"]
                
        for _, payout in self.df_payouts.iterrows():
            payout_utr = payout["utr"]
            if payout_utr in bank_utr_map:
                if payout["net_calculated"] == bank_utr_map[payout_utr]:
                    matched_payouts_to_bank += 1
                    
        order_match_rate = (matched_orders / total_orders) * 100 if total_orders > 0 else 0
        bank_match_rate = (matched_payouts_to_bank / total_bank_credits) * 100 if total_bank_credits > 0 else 0
        
        return {
            "baseline_matched_orders": matched_orders,
            "baseline_order_match_rate_pct": round(order_match_rate, 2),
            "baseline_matched_bank_credits": matched_payouts_to_bank,
            "baseline_bank_match_rate_pct": round(bank_match_rate, 2)
        }

    def run_deterministic_advanced(self):
        """
        Advanced deterministic matching logic (Equilibrium Phase 1).
        Enforces decimal precision, dynamic instrument MDR, business banking SLA,
        and batch disaggregation with anomaly isolation.
        """
        unresolved_records = {
            "orders": [],
            "payouts": [],
            "bank_credits": [],
            "bank_debits": []
        }
        
        matched_records = {
            "orders_to_payouts": [],
            "payouts_to_bank": [],
            "refund_debits": []
        }
        
        decision_traces = []
        
        stats = {
            "resolved_deterministically_orders": 0,
            "resolved_deterministically_payouts": 0,
            "resolved_deterministically_bank": 0,
            "total_orders": len(self.df_orders),
            "total_payouts": len(self.df_payouts),
            "total_bank_credits": len(self.df_bank[self.df_bank["amount_credited"] > 0]),
            "total_bank_debits": len(self.df_bank[self.df_bank["amount_debited"] > 0])
        }

        # --- A. Order to Payout Matching ---
        payouts_by_order = {}
        for _, p in self.df_payouts.iterrows():
            ord_id = p["order_id"]
            if ord_id not in payouts_by_order:
                payouts_by_order[ord_id] = []
            payouts_by_order[ord_id].append(p.to_dict())
            
        for _, order in self.df_orders.iterrows():
            ord_id = order["order_id"]
            order_dict = order.to_dict()
            
            payout_candidates = payouts_by_order.get(ord_id, [])
            primary_method = payout_candidates[0].get("payment_method", "credit_card") if payout_candidates else "credit_card"
            
            trace = {
                "step": "order_to_payout",
                "order_id": ord_id,
                "payment_method": primary_method,
                "amount": str(order["amount"]),
                "status": order["status"],
                "compared_candidates": [],
                "decision": "UNRESOLVED",
                "reason": ""
            }
            
            if ord_id not in payouts_by_order:
                if order["status"] == "failed":
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = "Failed order correctly lacks payout"
                    matched_records["orders_to_payouts"].append({
                        "order_id": ord_id,
                        "status": "resolved_no_payout",
                        "anomaly": None
                    })
                    stats["resolved_deterministically_orders"] += 1
                else:
                    trace["reason"] = "Captured order missing payout details"
                    unresolved_records["orders"].append(order_dict)
                decision_traces.append(trace)
                continue
                
            payout_candidates = payouts_by_order[ord_id]
            trace["compared_candidates"] = [
                {"payment_id": p["payment_id"], "amount": str(p["amount"]), "payment_method": p.get("payment_method", "credit_card")}
                for p in payout_candidates
            ]
            
            if len(payout_candidates) == 1:
                payout = payout_candidates[0]
                if order["amount"] == payout["amount"]:
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = f"Exact order_id and amount match ({payout.get('payment_method', 'card').upper()})"
                    matched_records["orders_to_payouts"].append({
                        "order_id": ord_id,
                        "payment_id": payout["payment_id"],
                        "payment_method": payout.get("payment_method", "credit_card"),
                        "amount": order["amount"],
                        "anomaly": None
                    })
                    stats["resolved_deterministically_orders"] += 1
                else:
                    trace["reason"] = "Amount mismatch between order and payout"
                    unresolved_records["orders"].append(order_dict)
            else:
                # Split Payments handling
                sum_payouts = sum(p["amount"] for p in payout_candidates)
                if order["amount"] == sum_payouts:
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = f"Split payment match: Sum of {len(payout_candidates)} payouts equals order amount"
                    for p in payout_candidates:
                        matched_records["orders_to_payouts"].append({
                            "order_id": ord_id,
                            "payment_id": p["payment_id"],
                            "payment_method": p.get("payment_method", "credit_card"),
                            "amount": p["amount"],
                            "anomaly": "split_payment"
                        })
                    stats["resolved_deterministically_orders"] += 1
                else:
                    trace["reason"] = "Sum of split payouts does not match order amount"
                    unresolved_records["orders"].append(order_dict)
                    
            decision_traces.append(trace)

        # --- B. Payout to Bank Statement Matching ---
        bank_credits = self.df_bank[self.df_bank["amount_credited"] > 0].copy()
        bank_credits["extracted_utr"] = bank_credits["description"].apply(extract_utr)
        bank_credits["extracted_set_id"] = bank_credits["description"].apply(extract_settlement_id)
        
        bank_credits_list = bank_credits.to_dict(orient="records")
        for b in bank_credits_list:
            b["reconciled"] = False
            
        payouts_list = self.df_payouts.to_dict(orient="records")
        for p in payouts_list:
            p["reconciled"] = False

        # 1. Match individual payout via unique UTR
        for p in payouts_list:
            p_utr = p["utr"]
            if not p_utr:
                continue
                
            trace = {
                "step": "payout_to_bank_utr",
                "payment_id": p["payment_id"],
                "payment_method": p.get("payment_method", "credit_card"),
                "utr": p_utr,
                "net_calculated": str(p["net_calculated"]),
                "compared_candidates": [],
                "decision": "UNRESOLVED",
                "reason": ""
            }
            
            matching_banks = [b for b in bank_credits_list if b["extracted_utr"] == p_utr]
            
            trace["compared_candidates"] = [
                {"date": b["date"], "amount_credited": str(b["amount_credited"]), "description": b["description"]}
                for b in matching_banks
            ]
            
            if len(matching_banks) == 1:
                bank_row = matching_banks[0]
                amount_diff = abs(p["net_calculated"] - bank_row["amount_credited"])
                
                # Check timing SLA using banking business days
                p_date = parse_date(p["settled_at"])
                b_date = parse_date(bank_row["date"])
                sla_valid = is_within_banking_sla(p_date, b_date, max_business_days=2)
                
                # Dynamic MDR Fee Compliance Check
                contract_fee_matches = (p["fee"] == p["contract_fee"]) and (p["tax_gst"] == p["contract_tax"])
                method_name = p.get("payment_method", "card").upper()
                
                if amount_diff == Decimal("0.00") and sla_valid and contract_fee_matches:
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = f"Exact UTR, {method_name} MDR compliance, and T+2 banking SLA verified"
                    p["reconciled"] = True
                    bank_row["reconciled"] = True
                    matched_records["payouts_to_bank"].append({
                        "payment_id": p["payment_id"],
                        "payment_method": p.get("payment_method", "credit_card"),
                        "utr": p_utr,
                        "amount_credited": bank_row["amount_credited"],
                        "anomaly": None
                    })
                    stats["resolved_deterministically_payouts"] += 1
                    stats["resolved_deterministically_bank"] += 1
                elif amount_diff == Decimal("0.01") and sla_valid and contract_fee_matches:
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = "Matched with 0.01 INR rounding discrepancy"
                    p["reconciled"] = True
                    bank_row["reconciled"] = True
                    matched_records["payouts_to_bank"].append({
                        "payment_id": p["payment_id"],
                        "payment_method": p.get("payment_method", "credit_card"),
                        "utr": p_utr,
                        "amount_credited": bank_row["amount_credited"],
                        "anomaly": "rounding_discrepancy"
                    })
                    stats["resolved_deterministically_payouts"] += 1
                    stats["resolved_deterministically_bank"] += 1
                elif not contract_fee_matches:
                    trace["reason"] = f"MDR Contract Violation: expected {method_name} fee {p['contract_fee']} vs actual gateway fee {p['fee']}"
                elif amount_diff > Decimal("0.01") and sla_valid:
                    trace["reason"] = f"MDR Fee mismatch: expected {p['net_calculated']} vs bank got {bank_row['amount_credited']}"
                else:
                    trace["reason"] = f"SLA Violation: bank transfer exceeded 2 business banking days (settled {p['settled_at']} vs bank date {bank_row['date']})"
            
            decision_traces.append(trace)

        # 2. Batch Settlement & Disaggregation Matching
        unreconciled_payouts = [p for p in payouts_list if not p["reconciled"]]
        unreconciled_banks = [b for b in bank_credits_list if not b["reconciled"]]
        
        payouts_by_settlement = {}
        for p in unreconciled_payouts:
            s_id = p["settlement_id"]
            if s_id not in payouts_by_settlement:
                payouts_by_settlement[s_id] = []
            payouts_by_settlement[s_id].append(p)
            
        for s_id, p_batch in payouts_by_settlement.items():
            matching_banks = [b for b in unreconciled_banks if b["extracted_set_id"] == s_id.lower()]
            sum_payout_net = sum(p["net_calculated"] for p in p_batch)
            
            trace = {
                "step": "payout_to_bank_settlement_batch",
                "settlement_id": s_id,
                "payment_method": "batch",
                "batch_size": len(p_batch),
                "sum_net_calculated": str(sum_payout_net),
                "compared_candidates": [
                    {"date": b["date"], "amount_credited": str(b["amount_credited"]), "description": b["description"]}
                    for b in matching_banks
                ],
                "decision": "UNRESOLVED",
                "reason": ""
            }
            
            if len(matching_banks) == 1:
                bank_row = matching_banks[0]
                amount_diff = abs(sum_payout_net - bank_row["amount_credited"])
                
                max_payout_settled = max(parse_date(p["settled_at"]) for p in p_batch)
                b_date = parse_date(bank_row["date"])
                sla_valid = is_within_banking_sla(max_payout_settled, b_date, max_business_days=2)
                
                # Check fee compliance per item in batch
                fee_compliant_payouts = [
                    p for p in p_batch
                    if (p["fee"] == p["contract_fee"]) and (p["tax_gst"] == p["contract_tax"])
                ]
                anomalous_payouts = [p for p in p_batch if p not in fee_compliant_payouts]
                
                if amount_diff == Decimal("0.00") and sla_valid and len(anomalous_payouts) == 0:
                    # Clean full batch
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = f"Batch settlement match: Sum of {len(p_batch)} multi-instrument payouts matches bank deposit exactly"
                    bank_row["reconciled"] = True
                    for p in p_batch:
                        p["reconciled"] = True
                        matched_records["payouts_to_bank"].append({
                            "payment_id": p["payment_id"],
                            "payment_method": p.get("payment_method", "credit_card"),
                            "utr": p["utr"],
                            "amount_credited": bank_row["amount_credited"],
                            "anomaly": "batch_settlement"
                        })
                        stats["resolved_deterministically_payouts"] += 1
                    stats["resolved_deterministically_bank"] += 1
                
                elif len(anomalous_payouts) > 0 and sla_valid:
                    # Batch Disaggregation: Isolate anomalous outlier, commit clean payouts
                    trace["decision"] = "PARTIAL_DISAGGREGATED"
                    trace["reason"] = f"Batch Disaggregation: Auto-committed {len(fee_compliant_payouts)} clean payouts; isolated {len(anomalous_payouts)} fee anomaly outlier(s)"
                    for p in fee_compliant_payouts:
                        p["reconciled"] = True
                        matched_records["payouts_to_bank"].append({
                            "payment_id": p["payment_id"],
                            "payment_method": p.get("payment_method", "credit_card"),
                            "utr": p["utr"],
                            "amount_credited": bank_row["amount_credited"],
                            "anomaly": "batch_disaggregated_clean"
                        })
                        stats["resolved_deterministically_payouts"] += 1
                    # Outliers remain unreconciled and flow to LLM exception review
                else:
                    trace["reason"] = f"Batch amount mismatch: expected sum {sum_payout_net} vs bank got {bank_row['amount_credited']}"
            else:
                trace["reason"] = f"No unique bank credit candidate for settlement ID {s_id}"
                
            decision_traces.append(trace)

        # --- C. Refund Debits Matching ---
        bank_debits = self.df_bank[self.df_bank["amount_debited"] > 0].copy()
        bank_debits["extracted_utr"] = bank_debits["description"].apply(extract_utr)
        
        bank_debits_list = bank_debits.to_dict(orient="records")
        for b in bank_debits_list:
            b["reconciled"] = False
            
        for b in bank_debits_list:
            utr = b["extracted_utr"]
            trace = {
                "step": "bank_debit_to_refund",
                "payment_method": "refund",
                "bank_date": b["date"],
                "amount_debited": str(b["amount_debited"]),
                "utr": utr,
                "compared_candidates": [],
                "decision": "UNRESOLVED",
                "reason": ""
            }
            
            matching_payouts = [p for p in payouts_list if p["utr"] == utr]
            trace["compared_candidates"] = [
                {"payment_id": p["payment_id"], "amount": str(p["amount"]), "order_id": p["order_id"]}
                for p in matching_payouts
            ]
            
            if len(matching_payouts) == 1:
                payout = matching_payouts[0]
                if payout["amount"] == b["amount_debited"]:
                    trace["decision"] = "AUTO_COMMIT"
                    trace["reason"] = "Exact UTR and original transaction amount match"
                    b["reconciled"] = True
                    matched_records["refund_debits"].append({
                        "utr": utr,
                        "payment_id": payout["payment_id"],
                        "amount_debited": b["amount_debited"],
                        "anomaly": "refund_match"
                    })
                    stats["resolved_deterministically_bank"] += 1
                else:
                    trace["reason"] = f"Refund amount mismatch: original payout {payout['amount']} vs bank debit {b['amount_debited']}"
            else:
                trace["reason"] = "Could not map refund to a unique payout record via UTR"
                
            decision_traces.append(trace)

        # Assemble final lists of unresolved records for LLM evaluation
        for p in payouts_list:
            if not p["reconciled"]:
                unresolved_records["payouts"].append(p)
        for b in bank_credits_list:
            if not b["reconciled"]:
                unresolved_records["bank_credits"].append(b)
        for b in bank_debits_list:
            if not b["reconciled"]:
                unresolved_records["bank_debits"].append(b)

        return matched_records, unresolved_records, decision_traces, stats
