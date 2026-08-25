import os
import json
import logging
from decimal import Decimal
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError
from openai import OpenAI
from dotenv import load_dotenv

# Setup logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Auditor")

# Load local environment if present
load_dotenv()

class MatchProposal(BaseModel):
    proposed_match: bool = Field(description="Set to true if a candidate maps to the unresolved record.")
    confidence_score: int = Field(description="Confidence score from 0 to 100 based on trace evidence.")
    justification: str = Field(description="Trace explanation detailing compared fields and specific mismatches.")

class ForensicAuditor:
    def __init__(self, api_key=None):
        # 1. Determine key and API provider
        # Prioritize DeepSeek if key is present
        self.deepseek_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.gemini_key = api_key or os.getenv("GEMINI_API_KEY")
        
        self.client = None
        self.client_type = None
        
        # Token and cost tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        
        if self.deepseek_key and not api_key: # If DEEPSEEK_API_KEY is found in env
            try:
                self.client = OpenAI(api_key=self.deepseek_key, base_url="https://api.deepseek.com/v1")
                self.client_type = "deepseek"
                logger.info("Initialized DeepSeek client.")
            except Exception as e:
                logger.error(f"Failed to initialize DeepSeek Client: {e}")
        elif self.gemini_key:
            try:
                self.client = genai.Client(api_key=self.gemini_key)
                self.client_type = "gemini"
                logger.info("Initialized Gemini client.")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")
        else:
            logger.warning("No valid API keys found (GEMINI_API_KEY or DEEPSEEK_API_KEY). LLM Auditor will run in dry-run mode.")

    def _track_cost(self, response_or_usage):
        """
        Calculates and accumulates actual model execution costs.
        """
        if self.client_type == "gemini":
            if not response_or_usage or not hasattr(response_or_usage, "usage_metadata"):
                return
            usage = response_or_usage.usage_metadata
            input_tokens = usage.prompt_token_count or 0
            output_tokens = usage.candidates_token_count or 0
            
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            
            # Gemini 2.5 Flash rates: input $0.075/1M, output $0.30/1M
            cost = (input_tokens * 0.075 / 1_000_000) + (output_tokens * 0.30 / 1_000_000)
            self.total_cost += cost
            
        elif self.client_type == "deepseek":
            if not response_or_usage or not hasattr(response_or_usage, "usage"):
                return
            usage = response_or_usage.usage
            input_tokens = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
            
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            
            # DeepSeek Chat rates (non-cached): input $0.55/1M, output $2.19/1M
            cost = (input_tokens * 0.55 / 1_000_000) + (output_tokens * 2.19 / 1_000_000)
            self.total_cost += cost

    def audit_unresolved_payout(self, payout, bank_candidates):
        """
        Audits an unresolved payout against potential bank statement deposits.
        """
        if not self.client:
            return MatchProposal(
                proposed_match=False,
                confidence_score=0,
                justification="LLM Client not authenticated. Defaulted to Abstained."
            )
            
        prompt = f"""
Analyze the following unresolved payment payout from the gateway reports and determine if it maps to any of the candidate bank statement deposits.

Unresolved Payout details:
- Payment ID: {payout.get('payment_id')}
- Expected Net Payout: {payout.get('net_calculated')} INR (Gross Amount: {payout.get('amount')}, Fee: {payout.get('fee')}, GST: {payout.get('tax_gst')})
- Settlement Date (from gateway): {payout.get('settled_at')}
- Settlement ID (from gateway): {payout.get('settlement_id')}
- UTR Reference: {payout.get('utr')}

Potential Bank Statement Candidates:
"""
        for idx, candidate in enumerate(bank_candidates):
            prompt += f"""
Candidate [{idx}]:
- Date: {candidate.get('date')}
- Amount Credited: {candidate.get('amount_credited')} INR
- Description Narration: {candidate.get('description')}
"""

        prompt += """
Rule Guidelines:
1. Bank transfer date must be within T+2 days of settlement date, allowing up to 4 days for weekend bank delays.
2. Check for fuzzy merchant names in the narration (e.g. "RZRPY PAYOUT", "RAZORPAY APY" are equivalent).
3. Check for fee mismatches (MDR Leakage) where the bank credited amount is lower than the expected net payout, but the UTR in the bank description matches the payout UTR.
4. Verify duplicates to avoid matching the wrong order ID for transactions with identical amounts.

Evaluate each candidate. Select the best match and output a structured JSON response matching the following schema:
{
  "proposed_match": boolean,
  "confidence_score": integer (0 to 100),
  "justification": "text trace explaining compared fields and specific mismatches"
}
If no candidate qualifies with high confidence, set proposed_match to false.
"""

        try:
            if self.client_type == "gemini":
                response = self.client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MatchProposal,
                        temperature=0.1
                    )
                )
                self._track_cost(response)
                result_dict = json.loads(response.text)
                return MatchProposal(**result_dict)
                
            elif self.client_type == "deepseek":
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a forensic financial auditor. You must output only a valid JSON object matching the requested schema. No markdown formatting, no code fences, just raw JSON."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                self._track_cost(response)
                result_dict = json.loads(response.choices[0].message.content)
                return MatchProposal(**result_dict)
            
        except Exception as e:
            logger.error(f"Unexpected error during payout audit: {e}")
            return MatchProposal(
                proposed_match=False,
                confidence_score=0,
                justification=f"Audit execution error: {str(e)}"
            )

    def audit_unresolved_refund(self, bank_debit, payout_candidates):
        """
        Audits an unresolved bank statement debit (refund) against payout candidates.
        """
        if not self.client:
            return MatchProposal(
                proposed_match=False,
                confidence_score=0,
                justification="LLM Client not authenticated. Defaulted to Abstained."
            )

        prompt = f"""
Analyze this unresolved bank statement debit (refund) and map it back to the original payment record.

Unresolved Bank Debit details:
- Date: {bank_debit.get('date')}
- Amount Debited: {bank_debit.get('amount_debited')} INR
- Description Narration: {bank_debit.get('description')}

Original Payment Candidates (Payouts):
"""
        for idx, candidate in enumerate(payout_candidates):
            prompt += f"""
Candidate [{idx}]:
- Payment ID: {candidate.get('payment_id')}
- Order ID: {candidate.get('order_id')}
- Gross Amount: {candidate.get('amount')} INR
- Settlement Date: {candidate.get('settled_at')}
- UTR Reference: {candidate.get('utr')}
"""

        prompt += """
Rule Guidelines:
1. The debit amount usually matches the gross original payment amount.
2. The refund transaction occurs AFTER the original payment settlement date.
3. Check if the UTR extracted from the bank description matches the candidate's UTR.
4. Watch out for duplicate transaction collisions. Ensure you link the refund to the correct transaction timestamp.

Evaluate each candidate. Select the best match and output a structured JSON response matching the following schema:
{
  "proposed_match": boolean,
  "confidence_score": integer (0 to 100),
  "justification": "text trace explaining compared fields and specific mismatches"
}
If no candidate matches, set proposed_match to false.
"""

        try:
            if self.client_type == "gemini":
                response = self.client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MatchProposal,
                        temperature=0.1
                    )
                )
                self._track_cost(response)
                result_dict = json.loads(response.text)
                return MatchProposal(**result_dict)
                
            elif self.client_type == "deepseek":
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a forensic financial auditor. You must output only a valid JSON object matching the requested schema. No markdown formatting, no code fences, just raw JSON."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                self._track_cost(response)
                result_dict = json.loads(response.choices[0].message.content)
                return MatchProposal(**result_dict)
            
        except Exception as e:
            logger.error(f"Unexpected error during refund audit: {e}")
            return MatchProposal(
                proposed_match=False,
                confidence_score=0,
                justification=f"Audit execution error: {str(e)}"
            )
