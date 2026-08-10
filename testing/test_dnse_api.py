import hmac
import hashlib
import base64
import uuid
import urllib.parse
from datetime import datetime, timezone
import requests

# --- Configuration ---
API_KEY = "eyJvcmciOiJkbnNlIiwiaWQiOiJiMjA3YWM0NjM5ZTE0NTMxODQ0NjJkNDZlNDI3M2M3MSIsImgiOiJtdXJtdXIxMjgifQ=="
API_SECRET = "_A0eYxGHUrab-AmNrZEAjddbXPb0QQjZALPV57BEbRFaIlyT8HfQ2aPB9-_ZUi8SI5V5DvdcPG2bDGEutX6_yQ"
API_VERSION = "2026-05-07"
SYMBOL = "VCG"

# --- Generate Date header (UTC, HTTP date format) ---
now = datetime.now(timezone.utc)
date_value = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

# --- Generate nonce (UUID hex, no dashes) ---
nonce = uuid.uuid4().hex  # 32-char hex string

# --- Build request-target ---
path = f"/price/{SYMBOL}/trades/latest"
method = "get"
date_header_name = "date"

# --- Build signature string ---
signature_string = f"(request-target): {method} {path}\n{date_header_name}: {date_value}\nnonce: {nonce}"

# --- Compute HMAC-SHA256 and base64-encode ---
hmac_bytes = hmac.new(
    API_SECRET.encode("utf-8"),
    signature_string.encode("utf-8"),
    hashlib.sha256
).digest()
base64_sig = base64.b64encode(hmac_bytes).decode("utf-8")

# --- URL-encode the signature (equivalent to urllib.parse.quote with safe="") ---
escaped_sig = urllib.parse.quote(base64_sig, safe="")

# --- Build X-Signature header value ---
headers_list = f"(request-target) {date_header_name}"
x_signature = (
    f'Signature keyId="{API_KEY}",'
    f'algorithm="hmac-sha256",'
    f'headers="{headers_list}",'
    f'signature="{escaped_sig}",'
    f'nonce="{nonce}"'
)

# --- Build headers ---
headers = {
    "Date": date_value,
    "X-Signature": x_signature,
    "X-API-Key": API_KEY,
    "version": API_VERSION,
}

# --- Send the request ---
url = f"https://openapi.dnse.com.vn/price/{SYMBOL}/trades/latest"
response = requests.get(url, headers=headers)

print(f"Status: {response.status_code}")
print(response.json())