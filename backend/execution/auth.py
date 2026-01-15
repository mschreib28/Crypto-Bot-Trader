"""Kraken REST API authentication and signature generation."""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Dict, Optional

from backend.config import KRAKEN_API_KEY, KRAKEN_API_SECRET

logger = logging.getLogger(__name__)

# Kraken API base URL
KRAKEN_API_URL = "https://api.kraken.com"


# Module-level nonce counter to ensure monotonicity within process
_nonce_counter = 0
_last_nonce_time = 0


def generate_nonce() -> str:
    """
    Generate a monotonically increasing nonce for Kraken API requests.
    
    Uses current timestamp in milliseconds with a counter to ensure
    monotonicity even with rapid requests. For production, this should be
    coordinated via Redis (see Ticket 12) to prevent collisions across
    multiple processes.
    
    Returns:
        Nonce as string (milliseconds since epoch + counter)
    """
    global _nonce_counter, _last_nonce_time
    
    # Get current time in milliseconds
    current_time_ms = int(time.time() * 1000)
    
    # If time hasn't advanced, increment counter
    if current_time_ms <= _last_nonce_time:
        _nonce_counter += 1
    else:
        _nonce_counter = 0
        _last_nonce_time = current_time_ms
    
    # Combine time and counter to ensure monotonicity
    nonce = str(current_time_ms + _nonce_counter)
    return nonce


def sign_request(uri_path: str, post_data: Dict[str, str], api_secret: str) -> str:
    """
    Generate Kraken API signature (API-Sign header).
    
    Kraken signature scheme:
    1. SHA256 hash of (nonce + urlencoded post_data)
    2. Concatenate uri_path + SHA256 hash
    3. HMAC-SHA512 of the concatenated message using decoded API secret
    4. Base64 encode the HMAC result
    
    Args:
        uri_path: API endpoint path (e.g., "/0/private/Balance")
        post_data: Dictionary of POST parameters (must include 'nonce')
        api_secret: Base64-encoded API secret from Kraken
        
    Returns:
        Base64-encoded signature string for API-Sign header
    """
    # Encode POST data
    encoded_post_data = urllib.parse.urlencode(post_data)
    
    # SHA256 of (nonce + encoded_post_data)
    sha256_input = (post_data.get("nonce", "") + encoded_post_data).encode()
    sha256_hash = hashlib.sha256(sha256_input).digest()
    
    # Concatenate URI path and SHA256 hash
    message = uri_path.encode() + sha256_hash
    
    # Decode API secret (Kraken provides it as base64)
    try:
        api_secret_decoded = base64.b64decode(api_secret)
    except Exception as e:
        logger.error(f"Failed to decode API secret: {e}")
        raise ValueError("Invalid API secret format (must be base64)") from e
    
    # HMAC-SHA512 of the message
    hmac_sha512 = hmac.new(api_secret_decoded, message, hashlib.sha512)
    api_signature = base64.b64encode(hmac_sha512.digest()).decode()
    
    return api_signature


def get_auth_headers(
    uri_path: str, 
    post_data: Dict[str, str],
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate authentication headers for Kraken private API requests.
    
    Args:
        uri_path: API endpoint path
        post_data: Dictionary of POST parameters (must include 'nonce')
        api_key: Kraken API key (uses config if not provided)
        api_secret: Kraken API secret (uses config if not provided)
        
    Returns:
        Dictionary with API-Key and API-Sign headers
    """
    # Use provided credentials or fall back to config
    key = api_key or KRAKEN_API_KEY
    secret = api_secret or KRAKEN_API_SECRET
    
    if not key or not secret:
        raise ValueError(
            "Kraken API credentials not configured. "
            "Set KRAKEN_API_KEY and KRAKEN_API_SECRET environment variables, "
            "or pass them to KrakenClient."
        )
    
    api_signature = sign_request(uri_path, post_data, secret)
    
    headers = {
        "API-Key": key,
        "API-Sign": api_signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    
    return headers
