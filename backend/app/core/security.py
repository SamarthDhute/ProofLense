import hashlib
import hmac
import secrets


def generate_capability_token() -> str:
    """Generate a high-entropy capability bearer token (256-bit URL-safe string)."""
    return secrets.token_urlsafe(32)


def hash_capability_token(token: str) -> str:
    """Compute constant-time friendly SHA-256 hash of a capability token."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def verify_capability_token(plain_token: str, hashed_token: str) -> bool:
    """Verify raw token against stored SHA-256 hash using constant-time comparison."""
    candidate_hash = hash_capability_token(plain_token)
    return hmac.compare_digest(candidate_hash, hashed_token)
