import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

# Indian Banking Holidays (standard RTGS / settlement holidays)
INDIAN_BANKING_HOLIDAYS_2026 = {
    "2026-01-26", # Republic Day
    "2026-04-14", # Dr. Ambedkar Jayanti
    "2026-08-15", # Independence Day
    "2026-10-02", # Mahatma Gandhi Jayanti
    "2026-11-08", # Diwali / Laxmi Pujan
    "2026-12-25", # Christmas
}

# Standard Indian payment instrument contract rates
CONTRACT_MDR_RATES = {
    "upi": {"mdr": Decimal("0.00"), "gst": Decimal("0.00")},          # UPI: 0% MDR
    "debit_card": {"mdr": Decimal("0.005"), "gst": Decimal("0.18")},   # Debit Card: 0.5% + 18% GST
    "credit_card": {"mdr": Decimal("0.02"), "gst": Decimal("0.18")},   # Credit Card: 2.0% + 18% GST
    "card": {"mdr": Decimal("0.02"), "gst": Decimal("0.18")},          # Generic Card: 2.0%
    "netbanking": {"mdr": Decimal("0.018"), "gst": Decimal("0.18")},   # Netbanking: 1.8% + 18% GST
    "amex": {"mdr": Decimal("0.035"), "gst": Decimal("0.18")},         # Amex / International: 3.5% + 18% GST
}

def to_decimal(val):
    if val is None or val == "":
        return Decimal("0.00")
    # Clean currency formatting if any
    cleaned = re.sub(r"[^\d\.-]", "", str(val))
    return Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def parse_date(date_str):
    """
    Parses dates in standard formats: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.
    Returns datetime object or None if invalid.
    """
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt)
        except ValueError:
            continue
    return None

def normalize_text(text):
    """
    Cleans white spaces and converts to uppercase for matching descriptions.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip()).upper()

def extract_utr(description):
    """
    Extracts UTR patterns from bank statements (e.g. UTR_N00025, UTR_DUP_SUCCESS).
    """
    match = re.search(r"UTR_[A-Z0-9_]+", description, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    return None

def extract_settlement_id(description):
    """
    Extracts settlement IDs (e.g. set_101) from bank statement descriptions.
    """
    match = re.search(r"SET_[A-Z0-9_]+", description, re.IGNORECASE)
    if match:
        return match.group(0).lower()
    return None

def get_instrument_contract_rates(payment_method):
    """
    Returns standard MDR and GST rates for a payment instrument. Defaults to credit_card.
    """
    key = str(payment_method or "credit_card").strip().lower()
    return CONTRACT_MDR_RATES.get(key, CONTRACT_MDR_RATES["credit_card"])

def calculate_expected_fees(amount, payment_method="credit_card", apply_tds=False):
    """
    Calculates expected contract fee, GST, TDS, and net calculated amount.
    """
    amt = to_decimal(amount)
    rates = get_instrument_contract_rates(payment_method)
    
    fee = to_decimal(amt * rates["mdr"])
    gst = to_decimal(fee * rates["gst"])
    tds = to_decimal(amt * Decimal("0.001")) if apply_tds else Decimal("0.00")
    net = amt - fee - gst - tds
    return {
        "fee": fee,
        "tax_gst": gst,
        "tds": tds,
        "net_calculated": net
    }

def is_banking_business_day(dt):
    """
    Checks if date is an Indian banking day (not Saturday, Sunday, or Bank Holiday).
    """
    if dt.weekday() in (5, 6): # 5=Saturday, 6=Sunday
        return False
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in INDIAN_BANKING_HOLIDAYS_2026:
        return False
    return True

def count_banking_business_days(start_date, end_date):
    """
    Counts the number of business banking days elapsed between two dates.
    """
    if not start_date or not end_date:
        return 0
    cur = start_date.date() + timedelta(days=1)
    end = end_date.date()
    business_days = 0
    while cur <= end:
        if is_banking_business_day(datetime(cur.year, cur.month, cur.day)):
            business_days += 1
        cur += timedelta(days=1)
    return business_days

def is_within_banking_sla(settled_at_date, bank_date, max_business_days=2):
    """
    Validates if bank deposit arrived within standard business-day SLA.
    """
    if not settled_at_date or not bank_date:
        return False
    if bank_date < settled_at_date:
        # Bank deposit cannot precede gateway settlement by more than 0 days
        days_diff = (settled_at_date.date() - bank_date.date()).days
        return days_diff <= 0
    business_days = count_banking_business_days(settled_at_date, bank_date)
    return business_days <= max_business_days
