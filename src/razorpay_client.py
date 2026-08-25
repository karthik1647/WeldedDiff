import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

def to_decimal(val):
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class RazorpaySandboxClient:
    """
    Authentic Razorpay API Sandbox Mock Client.
    Outputs schema-accurate Razorpay API response payloads matching 
    /v1/orders, /v1/payments, and /v1/settlements endpoints.
    """
    def __init__(self, currency="INR", default_fee_rate=Decimal("0.02"), tax_rate=Decimal("0.18")):
        self.currency = currency
        self.default_fee_rate = default_fee_rate
        self.tax_rate = tax_rate

    def create_order_payload(self, order_id: str, amount_inr: Decimal, created_at: datetime) -> dict:
        """
        Returns a schema-compliant Razorpay Order entity (/v1/orders).
        Note: Razorpay API represents currency amounts in paise (1 INR = 100 paise).
        """
        amount_paise = int(amount_inr * 100)
        timestamp = int(created_at.timestamp())
        
        return {
            "id": order_id,
            "entity": "order",
            "amount": amount_paise,
            "amount_paid": amount_paise,
            "amount_due": 0,
            "currency": self.currency,
            "receipt": f"rcpt_{order_id.replace('ord_', '')}",
            "offer_id": None,
            "status": "paid",
            "attempts": 1,
            "notes": {
                "source": "welded_diff_checkout",
                "integration": "razorpay_sandbox"
            },
            "created_at": timestamp
        }

    def create_payment_payload(self, payment_id: str, order_id: str, amount_inr: Decimal, created_at: datetime, method: str = "upi") -> dict:
        """
        Returns a schema-compliant Razorpay Payment entity (/v1/payments).
        Calculates contractual 2% MDR fee and 18% GST tax in paise.
        """
        amount_paise = int(amount_inr * 100)
        fee_inr = to_decimal(amount_inr * self.default_fee_rate)
        tax_inr = to_decimal(fee_inr * self.tax_rate)
        
        fee_paise = int(fee_inr * 100)
        tax_paise = int(tax_inr * 100)
        timestamp = int(created_at.timestamp())
        
        return {
            "id": payment_id,
            "entity": "payment",
            "amount": amount_paise,
            "currency": self.currency,
            "status": "captured",
            "order_id": order_id,
            "invoice_id": None,
            "international": False,
            "method": method,
            "amount_refunded": 0,
            "refund_status": None,
            "captured": True,
            "description": f"Payment for {order_id}",
            "card_id": None,
            "bank": None if method == "upi" else "HDFC",
            "wallet": None,
            "vpa": f"user{payment_id.replace('pay_', '')}@upi" if method == "upi" else None,
            "email": f"customer_{payment_id.replace('pay_', '')}@example.com",
            "contact": "+919876543210",
            "fee": fee_paise,
            "tax": tax_paise,
            "error_code": None,
            "error_description": None,
            "error_source": None,
            "error_step": None,
            "error_reason": None,
            "created_at": timestamp
        }

    def create_settlement_payload(self, settlement_id: str, amount_inr: Decimal, utr: str, created_at: datetime) -> dict:
        """
        Returns a schema-compliant Razorpay Settlement entity (/v1/settlements).
        """
        amount_paise = int(amount_inr * 100)
        timestamp = int(created_at.timestamp())
        
        return {
            "id": settlement_id,
            "entity": "settlement",
            "amount": amount_paise,
            "status": "processed",
            "fees": 0,
            "tax": 0,
            "utr": utr,
            "created_at": timestamp
        }
