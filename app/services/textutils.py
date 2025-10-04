# app/services/textutils.py
import re
_CC_SETTLEMENT_RE = re.compile(r'^\s*credit\s*card\s*settlement\s*[-–—]\s*', re.IGNORECASE)

def is_cc_settlement(note: str | None) -> bool:
    s = (note or "").strip()
    if _CC_SETTLEMENT_RE.search(s):
        return True
    low = s.casefold()
    return ("credit card" in low) and ("settlement" in low)
