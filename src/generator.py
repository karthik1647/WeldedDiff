import os
import csv
import json
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from src.razorpay_client import RazorpaySandboxClient
from src.utils import to_decimal, calculate_expected_fees

# Define project directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

def generate_synthetic_data(num_records=100, seed=42, scenario="standard"):
    random.seed(seed)
    rzp_client = RazorpaySandboxClient()
    
    # Base datetime for starting transactions
    start_time = datetime(2026, 8, 20, 10, 0, 0)
    
    orders = []
    payouts = []
    bank_records = []
    
    # Maintain list of UTRs and settlement batches for bank statement mapping
    settlements = {} # settlement_id -> [payout_dicts]

    # Payment instruments distribution
    instruments = ["upi", "upi", "upi", "debit_card", "credit_card", "credit_card", "amex"]
    
    # 1. Generate Normal Transactions
    for i in range(1, num_records + 1):
        order_id = f"ord_{i:03d}"
        amount = to_decimal(random.randint(500, 25000))
        created_at = start_time + timedelta(hours=i * 2, minutes=random.randint(0, 59))
        
        # Payment method assignment
        # Keep pay_012 and pay_005 as credit_card for historical test anchors
        if i in (5, 12):
            method = "credit_card"
        elif scenario == "mdr_leakage" and i % 10 == 0:
            method = "credit_card"
        else:
            method = random.choice(instruments)

        fee_breakdown = calculate_expected_fees(amount, method, apply_tds=False)
        fee = fee_breakdown["fee"]
        tax_gst = fee_breakdown["tax_gst"]
        net_amount = fee_breakdown["net_calculated"]
        
        orders.append({
            "order_id": order_id,
            "amount": amount,
            "customer_name": f"Customer {i}",
            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "captured"
        })
        
        # Normal settlement delay (typically T+2 days)
        settled_at = created_at + timedelta(days=2, hours=random.randint(1, 4))
        settlement_id = f"set_{100 + (i // 10):03d}"  # Group 10 payments into one settlement
        utr = f"UTR_N{i:05d}"
        
        payouts.append({
            "payment_id": f"pay_{i:03d}",
            "order_id": order_id,
            "payment_method": method,
            "amount": amount,
            "fee": fee,
            "tax_gst": tax_gst,
            "settlement_id": settlement_id,
            "settled_at": settled_at.strftime("%Y-%m-%d %H:%M:%S"),
            "utr": utr
        })
        
        if settlement_id not in settlements:
            settlements[settlement_id] = []
        settlements[settlement_id].append({
            "payment_id": f"pay_{i:03d}",
            "net_amount": net_amount,
            "settled_at": settled_at,
            "utr": utr
        })

    # 2. Inject Anomalies
    
    # Anomaly A: Fee Overcharge (MDR Leakage)
    # Target index 5 (pay_005): Gateway charges 3% instead of 2%
    p_over = payouts[5]
    ord_over = orders[5]
    overcharged_fee = to_decimal(ord_over["amount"] * Decimal("0.03"))
    p_over["fee"] = overcharged_fee
    p_over["tax_gst"] = to_decimal(overcharged_fee * Decimal("0.18"))
    
    # Anomaly B: Rounding Discrepancy (Float mismatch)
    # Target index 15: Subtract 0.01 INR from the gateway log
    pouts_round = payouts[15]
    pouts_round["fee"] = to_decimal(pouts_round["fee"] - Decimal("0.01"))
    
    # Anomaly C: Timing Delay (Weekend/Bank Holiday SLA Breach)
    # Target index 25: settled_at is Friday evening, settles in bank log Tuesday next week (SLA = T+2 days)
    p_delay = payouts[25]
    order_delay = orders[25]
    fri_date = datetime(2026, 8, 28, 18, 30, 0)
    order_delay["created_at"] = fri_date.strftime("%Y-%m-%d %H:%M:%S")
    p_delay["settled_at"] = (fri_date + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    
    # Anomaly D: Split Payment
    # Target index 40: order_id is paid in two half-payments
    ord_split = orders[40]
    total_amount = ord_split["amount"]
    half_1 = to_decimal(total_amount / 2)
    half_2 = total_amount - half_1
    
    orig_payout = payouts[40]
    payouts.pop(40)
    
    split_payout_1 = orig_payout.copy()
    split_payout_1["payment_id"] = f"{orig_payout['payment_id']}_S1"
    split_payout_1["amount"] = half_1
    split_payout_1["fee"] = to_decimal(half_1 * Decimal("0.02"))
    split_payout_1["tax_gst"] = to_decimal(split_payout_1["fee"] * Decimal("0.18"))
    split_payout_1["utr"] = f"{orig_payout['utr']}_S1"
    
    split_payout_2 = orig_payout.copy()
    split_payout_2["payment_id"] = f"{orig_payout['payment_id']}_S2"
    split_payout_2["amount"] = half_2
    split_payout_2["fee"] = to_decimal(half_2 * Decimal("0.02"))
    split_payout_2["tax_gst"] = to_decimal(split_payout_2["fee"] * Decimal("0.18"))
    split_payout_2["utr"] = f"{orig_payout['utr']}_S2"
    
    payouts.insert(40, split_payout_1)
    payouts.insert(41, split_payout_2)
    
    # Update settlements map for split payouts
    settlements[orig_payout["settlement_id"]] = [
        item for item in settlements[orig_payout["settlement_id"]] if item["utr"] != orig_payout["utr"]
    ]
    settlements[orig_payout["settlement_id"]].append({
        "payment_id": split_payout_1["payment_id"],
        "net_amount": half_1 - split_payout_1["fee"] - split_payout_1["tax_gst"],
        "settled_at": datetime.strptime(split_payout_1["settled_at"], "%Y-%m-%d %H:%M:%S"),
        "utr": split_payout_1["utr"]
    })
    settlements[orig_payout["settlement_id"]].append({
        "payment_id": split_payout_2["payment_id"],
        "net_amount": half_2 - split_payout_2["fee"] - split_payout_2["tax_gst"],
        "settled_at": datetime.strptime(split_payout_2["settled_at"], "%Y-%m-%d %H:%M:%S"),
        "utr": split_payout_2["utr"]
    })

    # Planted Hard Case: Duplicate Collision and Refund
    dup_time = datetime(2026, 8, 27, 14, 0, 0)
    orders.append({
        "order_id": "ord_dup_1",
        "amount": to_decimal(1500),
        "customer_name": "Karthik Dup Check",
        "created_at": dup_time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "captured"
    })
    orders.append({
        "order_id": "ord_dup_2",
        "amount": to_decimal(1500),
        "customer_name": "Karthik Dup Check",
        "created_at": (dup_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
        "status": "failed"
    })
    
    dup_fee = to_decimal(Decimal("1500") * Decimal("0.02"))
    dup_tax = to_decimal(dup_fee * Decimal("0.18"))
    dup_net = Decimal("1500") - dup_fee - dup_tax
    payouts.append({
        "payment_id": "pay_dup_1",
        "order_id": "ord_dup_1",
        "payment_method": "credit_card",
        "amount": to_decimal(1500),
        "fee": dup_fee,
        "tax_gst": dup_tax,
        "settlement_id": "set_dup",
        "settled_at": (dup_time + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "utr": "UTR_DUP_SUCCESS"
    })
    settlements["set_dup"] = [{
        "payment_id": "pay_dup_1",
        "net_amount": dup_net,
        "settled_at": dup_time + timedelta(days=2),
        "utr": "UTR_DUP_SUCCESS"
    }]
    
    # 3. Compile Bank Statements from settlements mapping
    for sett_id, payouts_in_settlement in settlements.items():
        total_deposit = sum(item["net_amount"] for item in payouts_in_settlement)
        max_settled_at = max(item["settled_at"] for item in payouts_in_settlement)
        
        transfer_date = max_settled_at
        if max_settled_at.weekday() == 5: # Saturday
            transfer_date = max_settled_at + timedelta(days=2)
        elif max_settled_at.weekday() == 6: # Sunday
            transfer_date = max_settled_at + timedelta(days=1)
            
        utrs_in_batch = [item["utr"] for item in payouts_in_settlement]
        if "UTR_N00025" in utrs_in_batch or any("N00025" in u for u in utrs_in_batch):
            transfer_date = datetime(2026, 9, 1, 11, 0, 0)
            
        fuzzy_names = [
            "NEFT-CMS-RAZORPAY-SETL",
            "RAZORPAY PAYOUTS",
            "RZRPY SETTLEMENT",
            "INF-RAZORPAY SOFTWARE"
        ]
        desc = f"CMS/{random.choice(fuzzy_names)}/{sett_id}/"
        if len(payouts_in_settlement) == 1:
            desc += payouts_in_settlement[0]["utr"]
        else:
            desc += "BATCH"

        bank_records.append({
            "date": transfer_date.strftime("%Y-%m-%d"),
            "description": desc,
            "amount_credited": to_decimal(total_deposit),
            "amount_debited": to_decimal(0)
        })

    # Refund debit for duplicate case
    refund_time = dup_time + timedelta(days=3)
    bank_records.append({
        "date": refund_time.strftime("%Y-%m-%d"),
        "description": "REFUND/CMS/RZRPY PAYOUT/UTR_DUP_SUCCESS/CANCELLED",
        "amount_credited": to_decimal(0),
        "amount_debited": to_decimal(1500)
    })

    # Write files to CSV
    write_csv(os.path.join(DATA_DIR, "internal_orders.csv"), orders[0].keys(), orders)
    write_csv(os.path.join(DATA_DIR, "razorpay_payouts.csv"), payouts[0].keys(), payouts)
    write_csv(os.path.join(DATA_DIR, "bank_statements.csv"), bank_records[0].keys(), bank_records)
    print("Synthetic transactional data with dynamic instruments generated successfully.")

def write_csv(filepath, fieldnames, records):
    with open(filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

if __name__ == "__main__":
    generate_synthetic_data()
