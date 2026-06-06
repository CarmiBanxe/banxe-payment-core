"""HMAC-SHA256 checksum for Paymentology Companion API."""
import hashlib
import hmac


def compute_checksum(method_name: str, params: list[str], terminal_password: str) -> str:
    message = method_name + "".join(str(p) for p in params)
    signature = hmac.new(
        terminal_password.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return signature
