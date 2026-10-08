import re
import hmac
import hashlib
from typing import Optional, Dict, Any
from app.config import settings

def normalize_email(email: Optional[str]) -> str:
    """
    Normalize email: lowercase + strip whitespace everywhere.
    """
    if not email:
        return ""
    return email.strip().lower()

def generate_identity(email: str, full_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Deterministic identity generation using HMAC-SHA256 with SHARED_IDENTITY_SALT.
    Same email produces identical fake identity across NiftyTrade, BharatInvest,
    BondBazaar, and TradeOne.
    Full name from Google name claim when available, otherwise email local-part.
    Never generates real-looking Aadhaar/PAN data.
    """
    norm_email = normalize_email(email)
    salt = getattr(settings, "SHARED_IDENTITY_SALT", "tradeone-shared-salt-2026")
    digest = hmac.new(salt.encode("utf-8"), norm_email.encode("utf-8"), hashlib.sha256).hexdigest()

    # Full name derivation
    if full_name and full_name.strip():
        name = full_name.strip()
    else:
        local_part = norm_email.split("@")[0] if "@" in norm_email else norm_email
        parts = [p.capitalize() for p in re.split(r'[\._\-+]', local_part) if p]
        name = " ".join(parts) if parts else local_part.capitalize()

    # Never generate real-looking PAN (real is 5 letters + 4 digits + 1 letter, total 10 chars)
    # Using explicit FAKEPAN prefix ensures it cannot be confused with real PAN data.
    pan_num = int(digest[0:8], 16) % 100000
    pan = f"FAKEPAN{pan_num:05d}"
    pan_masked = f"FAKEPANXX{pan_num % 100:02d}"

    # Never generate real-looking Aadhaar (real is 12 digits)
    # Using explicit FAKE prefix ensures it cannot be confused with real Aadhaar data.
    aadhaar_num = int(digest[8:16], 16) % 100000000
    aadhaar = f"FAKE{aadhaar_num:08d}"

    # Deterministic mobile
    mobile_num = int(digest[16:24], 16) % 100000000
    mobile = f"98{mobile_num:08d}"

    # Demat account using DP_ID
    dp_id = getattr(settings, "DP_ID", "IN300002")
    demat_num = int(digest[24:32], 16) % 100000000
    demat_account = f"{dp_id}{demat_num:08d}"

    # Deterministic client code for BharatInvest
    client_code = f"BI{10000 + (int(digest[32:36], 16) % 90000)}"

    return {
        "email": norm_email,
        "full_name": name,
        "name": name,
        "pan": pan,
        "pan_masked": pan_masked,
        "aadhaar": aadhaar,
        "mobile": mobile,
        "demat_account": demat_account,
        "client_code": client_code,
    }
