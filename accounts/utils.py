import secrets
import string
from django.contrib.auth.hashers import check_password, make_password

# Character pool excluding easily confused characters: 0, O, 1, I, L
OTP_CHARACTERS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def normalize_otp(raw_otp: str) -> str:
    """Normalize OTP by stripping whitespaces, hyphens, and converting to uppercase."""
    if not raw_otp:
        return ""
    cleaned = raw_otp.strip().upper().replace("-", "").replace(" ", "")
    if cleaned.startswith("PO"):
        cleaned = cleaned[2:]
    return cleaned


def generate_recovery_otp() -> str:
    """
    Generate a cryptographically secure, formatted recovery OTP/credential.
    Example: PO-8F2K-M3NP-X94W
    """
    chunk1 = "".join(secrets.choice(OTP_CHARACTERS) for _ in range(4))
    chunk2 = "".join(secrets.choice(OTP_CHARACTERS) for _ in range(4))
    chunk3 = "".join(secrets.choice(OTP_CHARACTERS) for _ in range(4))
    return f"PO-{chunk1}-{chunk2}-{chunk3}"


def hash_recovery_otp(raw_otp: str) -> str:
    """Hash the normalized recovery OTP using Django's password hasher."""
    normalized = normalize_otp(raw_otp)
    return make_password(normalized)


def verify_recovery_otp(raw_otp: str, stored_hash_or_secret: str) -> bool:
    """
    Verify the raw recovery OTP against the stored secret or hash.
    Supports both hashed secrets and direct plaintext match for resilience.
    """
    if not raw_otp or not stored_hash_or_secret:
        return False
    
    normalized_input = normalize_otp(raw_otp)
    
    # Try checking as a standard Django password hash
    try:
        if check_password(normalized_input, stored_hash_or_secret):
            return True
    except Exception:
        pass

    # Fallback to direct normalized match (in case legacy plaintext was saved)
    normalized_stored = normalize_otp(stored_hash_or_secret)
    return secrets.compare_digest(normalized_input, normalized_stored)


def generate_secure_password(length: int = 16) -> str:
    """Generate a high-entropy temporary or auto-generated password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        # Ensure at least one uppercase, lowercase, digit, and special char
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password
