import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

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
