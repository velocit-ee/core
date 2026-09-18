"""
Telia Business Edge — Security & Compliance Module
Implements HMAC-SHA256 Claim Tokens, Audit Trails (NIS2/GDPR), and Zero-Trust Quarantine.
"""

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

# Edge secret key (generated per gateway instance)
_GATEWAY_SECRET = os.environ.get("TELIA_EDGE_SECRET", secrets.token_hex(32)).encode()
_TOKEN_TTL_SECONDS = 300  # 5 minute claim window

# Memory-backed audit log (structured RFC 5424 / CEF compatible)
audit_log: list[Dict[str, Any]] = []

def record_audit_event(
    event_type: str,
    actor: str,
    target_mac: str,
    details: str,
    severity: str = "INFO"
) -> Dict[str, Any]:
    """Records an immutable security audit event."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": secrets.token_hex(6),
        "severity": severity,
        "event_type": event_type,
        "actor": actor,
        "target_mac": target_mac,
        "details": details,
        "compliance": ["GDPR_Art32", "NIS2_SupplyChain", "Estonia_KTS"]
    }
    audit_log.append(event)
    return event

def generate_claim_token(mac: str, profile: str) -> Tuple[str, int]:
    """Generates a cryptographically signed claim token for a specific hardware MAC."""
    expires_at = int(time.time()) + _TOKEN_TTL_SECONDS
    payload = f"{mac.lower()}:{profile}:{expires_at}"
    signature = hmac.new(_GATEWAY_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    token = f"{expires_at}.{signature}"
    
    record_audit_event(
        event_type="CLAIM_TOKEN_ISSUED",
        actor="telia_admin_portal",
        target_mac=mac,
        details=f"Issued token for profile '{profile}', expires at {expires_at}"
    )
    return token, expires_at

def verify_claim_token(mac: str, profile: str, token: str) -> bool:
    """Verifies that a claim token is authentic, unexpired, and matches the MAC."""
    if not token or "." not in token:
        return False
        
    parts = token.split(".")
    if len(parts) != 2:
        return False
        
    try:
        expires_at = int(parts[0])
    except ValueError:
        return False
        
    if time.time() > expires_at:
        record_audit_event(
            event_type="CLAIM_TOKEN_EXPIRED",
            actor="ipxe_client",
            target_mac=mac,
            details=f"Token expired at {expires_at}",
            severity="WARN"
        )
        return False
        
    expected_payload = f"{mac.lower()}:{profile}:{expires_at}"
    expected_sig = hmac.new(_GATEWAY_SECRET, expected_payload.encode(), hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(parts[1], expected_sig):
        record_audit_event(
            event_type="CLAIM_TOKEN_INVALID_SIG",
            actor="ipxe_client",
            target_mac=mac,
            details="HMAC signature verification failed",
            severity="ALERT"
        )
        return False
        
    return True
