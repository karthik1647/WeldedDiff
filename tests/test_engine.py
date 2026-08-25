import os
import pytest
from decimal import Decimal
import pandas as pd
from src.engine import ReconciliationEngine
from src.utils import to_decimal

# Create temporary mock csv files for localized tests
@pytest.fixture
def mock_ledger_files(tmp_path):
    orders_csv = tmp_path / "orders.csv"
    payouts_csv = tmp_path / "payouts.csv"
    bank_csv = tmp_path / "bank.csv"
    
    # Write mock data structure
    pd.DataFrame([]).to_csv(orders_csv, index=False)
    pd.DataFrame([]).to_csv(payouts_csv, index=False)
    pd.DataFrame([]).to_csv(bank_csv, index=False)
    
    return str(orders_csv), str(payouts_csv), str(bank_csv)

def test_to_decimal():
    assert to_decimal("150.00") == Decimal("150.00")
    assert to_decimal(150) == Decimal("150.00")
    assert to_decimal("150.004") == Decimal("150.00")
    assert to_decimal("150.005") == Decimal("150.01") # Half-up rounding
    assert to_decimal("") == Decimal("0.00")

def test_exact_payout_match(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # 1. Setup exact match
    orders_df = pd.DataFrame([{
        "order_id": "ord_101",
        "amount": 1000.00,
        "customer_name": "Test User",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([{
        "payment_id": "pay_101",
        "order_id": "ord_101",
        "amount": 1000.00,
        "fee": 20.00,
        "tax_gst": 3.60, # 18% of 20
        "settlement_id": "set_101",
        "settled_at": "2026-08-27 12:00:00",
        "utr": "UTR_TEST_101"
    }])
    # Expected net: 1000 - 20 - 3.60 = 976.40
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_101/UTR_TEST_101",
        "amount_credited": 976.40,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    assert stats["resolved_deterministically_orders"] == 1
    assert stats["resolved_deterministically_payouts"] == 1
    assert len(matched["orders_to_payouts"]) == 1
    assert len(matched["payouts_to_bank"]) == 1
    assert matched["payouts_to_bank"][0]["anomaly"] is None

def test_split_payment_match(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # Order amount is 5000, split into two payouts of 2500 each
    orders_df = pd.DataFrame([{
        "order_id": "ord_split",
        "amount": 5000.00,
        "customer_name": "Test Split",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([
        {
            "payment_id": "pay_split_1",
            "order_id": "ord_split",
            "amount": 2500.00,
            "fee": 50.00,
            "tax_gst": 9.00,
            "settlement_id": "set_split",
            "settled_at": "2026-08-27 12:00:00",
            "utr": "UTR_SPLIT_1"
        },
        {
            "payment_id": "pay_split_2",
            "order_id": "ord_split",
            "amount": 2500.00,
            "fee": 50.00,
            "tax_gst": 9.00,
            "settlement_id": "set_split",
            "settled_at": "2026-08-27 12:00:00",
            "utr": "UTR_SPLIT_2"
        }
    ])
    # Expected net sum: (2500-59)*2 = 4882.00
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_split/BATCH",
        "amount_credited": 4882.00,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    assert stats["resolved_deterministically_orders"] == 1
    assert stats["resolved_deterministically_payouts"] == 2
    assert matched["orders_to_payouts"][0]["anomaly"] == "split_payment"

def test_fee_overcharge_anomaly_not_auto_committed(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # expected fee 20, GST 3.60 -> expected net 976.40
    # but gateway overcharged fee (e.g. 30, GST 5.40 -> expected net 964.60)
    # bank got 964.60. The engine must NOT auto-commit this payout-to-bank match deterministically.
    orders_df = pd.DataFrame([{
        "order_id": "ord_leak",
        "amount": 1000.00,
        "customer_name": "Leak User",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([{
        "payment_id": "pay_leak",
        "order_id": "ord_leak",
        "amount": 1000.00,
        "fee": 30.00,
        "tax_gst": 5.40,
        "settlement_id": "set_leak",
        "settled_at": "2026-08-27 12:00:00",
        "utr": "UTR_LEAK"
    }])
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_leak/UTR_LEAK",
        # If the bank statement credited matches the overcharged net: 964.60,
        # but the standard fee calculation should have produced a different net,
        # this is flagged. In engine.py, a payout-to-bank mismatch goes to unresolved.
        "amount_credited": 964.60,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    # Payout should be unresolved, awaiting LLM forensic audit
    assert len(unresolved["payouts"]) == 1
    assert len(matched["payouts_to_bank"]) == 0

def test_rounding_discrepancy(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # Rounding discrepancy of exactly 0.01 INR
    orders_df = pd.DataFrame([{
        "order_id": "ord_round",
        "amount": 100.00,
        "customer_name": "Round User",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([{
        "payment_id": "pay_round",
        "order_id": "ord_round",
        "amount": 100.00,
        "fee": 2.00,
        "tax_gst": 0.36,
        "settlement_id": "set_round",
        "settled_at": "2026-08-27 12:00:00",
        "utr": "UTR_ROUND"
    }]) # net_calculated: 97.64
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_round/UTR_ROUND",
        "amount_credited": 97.63, # 0.01 mismatch
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    assert len(matched["payouts_to_bank"]) == 1
    assert matched["payouts_to_bank"][0]["anomaly"] == "rounding_discrepancy"

def test_timing_sla_boundary(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # Timing gap: Gateway settles Aug 25, but bank deposit date is Aug 31 (exceeds SLA threshold)
    orders_df = pd.DataFrame([{
        "order_id": "ord_sla",
        "amount": 100.00,
        "customer_name": "SLA User",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([{
        "payment_id": "pay_sla",
        "order_id": "ord_sla",
        "amount": 100.00,
        "fee": 2.00,
        "tax_gst": 0.36,
        "settlement_id": "set_sla",
        "settled_at": "2026-08-25 12:00:00",
        "utr": "UTR_SLA"
    }])
    bank_df = pd.DataFrame([{
        "date": "2026-08-31", # 6 days later, SLA breach
        "description": "CMS/RZRPY PAYOUT/set_sla/UTR_SLA",
        "amount_credited": 97.64,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    # Must go to unresolved because of SLA violation
    assert len(unresolved["payouts"]) == 1
    assert len(matched["payouts_to_bank"]) == 0
