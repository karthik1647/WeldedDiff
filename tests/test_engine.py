import os
import pytest
from decimal import Decimal
import pandas as pd
from src.engine import ReconciliationEngine
from src.utils import to_decimal, calculate_expected_fees, is_within_banking_sla, parse_date

@pytest.fixture
def mock_ledger_files(tmp_path):
    orders_csv = tmp_path / "orders.csv"
    payouts_csv = tmp_path / "payouts.csv"
    bank_csv = tmp_path / "bank.csv"
    
    pd.DataFrame([]).to_csv(orders_csv, index=False)
    pd.DataFrame([]).to_csv(payouts_csv, index=False)
    pd.DataFrame([]).to_csv(bank_csv, index=False)
    
    return str(orders_csv), str(payouts_csv), str(bank_csv)

def test_to_decimal():
    assert to_decimal("150.00") == Decimal("150.00")
    assert to_decimal(150) == Decimal("150.00")
    assert to_decimal("150.004") == Decimal("150.00")
    assert to_decimal("150.005") == Decimal("150.01")
    assert to_decimal("") == Decimal("0.00")

def test_dynamic_instrument_mdr():
    # UPI: 0% fee
    upi_calc = calculate_expected_fees(1000, "upi")
    assert upi_calc["fee"] == Decimal("0.00")
    assert upi_calc["tax_gst"] == Decimal("0.00")
    assert upi_calc["net_calculated"] == Decimal("1000.00")

    # Debit Card: 0.5% MDR + 18% GST -> 5.00 + 0.90 = 5.90
    debit_calc = calculate_expected_fees(1000, "debit_card")
    assert debit_calc["fee"] == Decimal("5.00")
    assert debit_calc["tax_gst"] == Decimal("0.90")
    assert debit_calc["net_calculated"] == Decimal("994.10")

    # Credit Card: 2.0% MDR + 18% GST -> 20.00 + 3.60 = 23.60
    card_calc = calculate_expected_fees(1000, "credit_card")
    assert card_calc["fee"] == Decimal("20.00")
    assert card_calc["tax_gst"] == Decimal("3.60")
    assert card_calc["net_calculated"] == Decimal("976.40")

    # Amex: 3.5% MDR + 18% GST -> 35.00 + 6.30 = 41.30
    amex_calc = calculate_expected_fees(1000, "amex")
    assert amex_calc["fee"] == Decimal("35.00")
    assert amex_calc["tax_gst"] == Decimal("6.30")
    assert amex_calc["net_calculated"] == Decimal("958.70")

def test_banking_business_day_sla():
    # Friday to Monday = 1 business day (Saturday/Sunday skipped) -> Valid within T+2 SLA
    fri = parse_date("2026-08-21 14:00:00")
    mon = parse_date("2026-08-24 11:00:00")
    assert is_within_banking_sla(fri, mon, max_business_days=2) is True

    # Friday to Tuesday = 2 business days -> Valid within T+2 SLA
    tue = parse_date("2026-08-25 11:00:00")
    assert is_within_banking_sla(fri, tue, max_business_days=2) is True

    # Friday to Thursday next week = 4 business days -> Breach T+2 SLA
    thu = parse_date("2026-08-27 11:00:00")
    assert is_within_banking_sla(fri, thu, max_business_days=2) is False

def test_exact_payout_match_upi(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    orders_df = pd.DataFrame([{
        "order_id": "ord_upi_101",
        "amount": 1000.00,
        "customer_name": "Test User UPI",
        "created_at": "2026-08-25 10:00:00",
        "status": "captured"
    }])
    payouts_df = pd.DataFrame([{
        "payment_id": "pay_upi_101",
        "order_id": "ord_upi_101",
        "payment_method": "upi",
        "amount": 1000.00,
        "fee": 0.00,
        "tax_gst": 0.00,
        "settlement_id": "set_upi_101",
        "settled_at": "2026-08-27 12:00:00",
        "utr": "UTR_UPI_101"
    }])
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_upi_101/UTR_UPI_101",
        "amount_credited": 1000.00,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    assert stats["resolved_deterministically_orders"] == 1
    assert stats["resolved_deterministically_payouts"] == 1
    assert len(matched["payouts_to_bank"]) == 1
    assert matched["payouts_to_bank"][0]["payment_method"] == "upi"

def test_batch_disaggregation_isolates_faulty_transaction(mock_ledger_files):
    orders_path, payouts_path, bank_path = mock_ledger_files
    
    # 3 payouts in batch set_batch: 2 clean (pay_b1, pay_b2) and 1 overcharged (pay_b3)
    orders_df = pd.DataFrame([
        {"order_id": "ord_b1", "amount": 1000.00, "customer_name": "U1", "created_at": "2026-08-25 10:00:00", "status": "captured"},
        {"order_id": "ord_b2", "amount": 2000.00, "customer_name": "U2", "created_at": "2026-08-25 10:00:00", "status": "captured"},
        {"order_id": "ord_b3", "amount": 1500.00, "customer_name": "U3", "created_at": "2026-08-25 10:00:00", "status": "captured"}
    ])
    payouts_df = pd.DataFrame([
        # Clean pay_b1: credit_card 2% fee (20 + 3.60 = 23.60) -> net 976.40
        {"payment_id": "pay_b1", "order_id": "ord_b1", "payment_method": "credit_card", "amount": 1000.00, "fee": 20.00, "tax_gst": 3.60, "settlement_id": "set_batch", "settled_at": "2026-08-27 12:00:00", "utr": "UTR_B1"},
        # Clean pay_b2: upi 0% fee -> net 2000.00
        {"payment_id": "pay_b2", "order_id": "ord_b2", "payment_method": "upi", "amount": 2000.00, "fee": 0.00, "tax_gst": 0.00, "settlement_id": "set_batch", "settled_at": "2026-08-27 12:00:00", "utr": "UTR_B2"},
        # Faulty pay_b3: overcharged fee (50.00 instead of 30.00 on card) -> net 1441.00 instead of 1464.60
        {"payment_id": "pay_b3", "order_id": "ord_b3", "payment_method": "credit_card", "amount": 1500.00, "fee": 50.00, "tax_gst": 9.00, "settlement_id": "set_batch", "settled_at": "2026-08-27 12:00:00", "utr": "UTR_B3"}
    ])
    # Bank got sum: 976.40 + 2000.00 + 1441.00 = 4417.40
    bank_df = pd.DataFrame([{
        "date": "2026-08-27",
        "description": "CMS/RZRPY PAYOUT/set_batch/BATCH",
        "amount_credited": 4417.40,
        "amount_debited": 0.00
    }])
    
    orders_df.to_csv(orders_path, index=False)
    payouts_df.to_csv(payouts_path, index=False)
    bank_df.to_csv(bank_path, index=False)
    
    engine = ReconciliationEngine(orders_path, payouts_path, bank_path)
    matched, unresolved, _, stats = engine.run_deterministic_advanced()
    
    # 2 clean payouts are auto-committed via Batch Disaggregation!
    assert len(matched["payouts_to_bank"]) == 2
    matched_ids = [p["payment_id"] for p in matched["payouts_to_bank"]]
    assert "pay_b1" in matched_ids
    assert "pay_b2" in matched_ids
    
    # 1 faulty payout is isolated and sent to unresolved
    assert len(unresolved["payouts"]) == 1
    assert unresolved["payouts"][0]["payment_id"] == "pay_b3"

def test_fuzzy_bank_header_normalization():
    from src.utils import normalize_bank_csv_headers
    # Test HDFC / ICICI bank statement column variation
    raw_hdfc_df = pd.DataFrame([{
        "Txn Date": "2026-08-25",
        "Narration": "CMS/RZRPY/set_101/UTR_001",
        "Deposit Amt": "12500.00",
        "Withdrawal Amt": "0.00"
    }])
    normalized = normalize_bank_csv_headers(raw_hdfc_df)
    assert "date" in normalized.columns
    assert "description" in normalized.columns
    assert "amount_credited" in normalized.columns
    assert "amount_debited" in normalized.columns
    assert normalized.iloc[0]["amount_credited"] == "12500.00"

