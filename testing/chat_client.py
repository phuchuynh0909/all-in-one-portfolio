# chat_client.py
import base64
import re
import struct
import requests
import time
from protos_dynamic import build_dynamic_messages

# Build dynamic message classes
ChatRequest, ChatStreamEvent = build_dynamic_messages()

API_URL = "https://mkt-adv-api.mbs.com.vn/chat.v2.ChatbotCoreServiceV2/Chat"

def build_grpc_web_text_body(message_bytes: bytes) -> str:
    """
    Build gRPC-Web framed binary and then base64-encode it for grpc-web-text.
    Frame format: 1-byte flags + 4-byte big-endian length + message bytes.
    """
    flags = 0  # 0 for data frame
    length = len(message_bytes)
    frame = struct.pack(">B", flags) + struct.pack(">I", length) + message_bytes
    return base64.b64encode(frame).decode("ascii")

def _decode_grpc_web_text_body(b64_str: str) -> bytes:
    """
    Decode grpc-web-text body to binary. Frames are base64-encoded and concatenated;
    padding (=) marks boundaries. Split on =+, decode each part, concatenate.
    """
    b64_str = b64_str.replace("\n", "").replace("\r", "").strip()
    if not b64_str:
        return b""
    parts = re.split(r"=+", b64_str)
    chunks = []
    for p in parts:
        if not p:
            continue
        pad = (4 - len(p) % 4) % 4
        try:
            chunks.append(base64.b64decode(p + "=" * pad))
        except Exception:
            continue
    return b"".join(chunks)


def parse_grpc_web_text_stream(body: str, debug: bool = False):
    """
    Decode grpc-web-text response and yield raw message bytes for each data frame.
    Frames are base64-encoded and concatenated; padding (=) marks boundaries.
    Each binary frame: 1-byte flags + 4-byte length + payload. Skip trailers (0x80).
    """
    data = _decode_grpc_web_text_body(body)
    if debug:
        print(f"[DEBUG] parse_grpc_web_text_stream: body len={len(body)}, decoded len={len(data)}")

    frame_idx = 0
    offset = 0
    while offset + 5 <= len(data):
        flags = data[offset]
        (msg_len,) = struct.unpack(">I", data[offset + 1 : offset + 5])

        if offset + 5 + msg_len > len(data):
            if debug:
                print(f"[DEBUG] frame #{frame_idx}: malformed (msg_len={msg_len}, data_len={len(data)-offset})")
            break

        msg_bytes = data[offset + 5 : offset + 5 + msg_len]
        offset += 5 + msg_len

        if debug:
            print(f"[DEBUG] frame #{frame_idx}: flags=0x{flags:02x}, msg_len={msg_len}, is_trailers={bool(flags & 0x80)}")
        frame_idx += 1

        if flags & 0x80:
            continue  # trailers frame, skip

        yield msg_bytes

def call_chat_api(query: str, bearer_token: str, debug: bool = True):
    # Build ChatRequest protobuf
    req = ChatRequest()
    req.query = query

    # Serialize protobuf message
    req_bytes = req.SerializeToString()

    # Frame + base64-encode for grpc-web-text
    payload = build_grpc_web_text_body(req_bytes)

    if debug:
        print(f"[DEBUG] Request: query={query!r}, req_bytes len={len(req_bytes)}, payload len={len(payload)}")

    headers = {
        "Content-Type": "application/grpc-web-text",
        "Accept": "application/grpc-web-text",
        "X-Grpc-Web": "1",
        "X-User-Agent": "grpc-web-python/0.1",
        # copy any other headers you need (channel, client-id, etc.)
        "authorization": f"Bearer {bearer_token}",
    }

    # Use stream=True to receive full streaming response (avoids truncation / timeout)
    resp = requests.post(
        API_URL, data=payload, headers=headers,
        stream=True, timeout=120
    )
    resp.raise_for_status()

    if debug:
        print(f"[DEBUG] Response: status={resp.status_code}, headers={dict(resp.headers)}")

    # Accumulate full response body (streaming API sends chunked data)
    body_chunks = []
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            body_chunks.append(chunk)
    # grpc-web-text: each line is a separate base64-encoded frame (keep newlines for splitting)
    body_raw = b"".join(body_chunks).decode("ascii", errors="replace")

    if debug:
        preview = (body_raw[:200] + "...") if len(body_raw) > 200 else body_raw
        print(f"[DEBUG] Response body: {len(body_chunks)} chunks, {len(body_raw)} chars, preview={preview!r}")
        line_count = len([ln for ln in body_raw.splitlines() if ln.strip()])
        print(f"[DEBUG] Base64 lines (frames): {line_count}")

    events = []
    for i, msg_bytes in enumerate(parse_grpc_web_text_stream(body_raw, debug=debug)):
        try:
            event = ChatStreamEvent()
            event.ParseFromString(msg_bytes)
            events.append(event)
            if debug:
                print(f"[DEBUG] Parsed event #{i}: text={event.text!r}, eventType={event.eventType!r}, chatId={event.chatId!r}")
        except Exception as e:
            if debug:
                print(f"[DEBUG] Parse error for frame #{i} (len={len(msg_bytes)}): {e}")
                print(f"[DEBUG] Raw hex (first 100 bytes): {msg_bytes[:100].hex()}")

    if debug:
        print(f"[DEBUG] Total events parsed: {len(events)}")

    return events

if __name__ == "__main__":
    # TODO: set your real token
    TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJ5WENObDU5UVdjbFR3YmZCWnlmRmxLMlVDLTVKV1M1MjhJYVcwU0xfUTVzIn0.eyJleHAiOjE3NzA5Nzc1MjIsImlhdCI6MTc3MDk3NzIyMiwianRpIjoiMzJiYjEwZWQtZjhmZC00NDBmLWI0NDctOTdmYTI2Mzc4ODdmIiwiaXNzIjoiaHR0cHM6Ly9hY2N0cy5tYnMuY29tLnZuL2F1dGgvcmVhbG1zL3BlcmljbGVzIiwiYXVkIjoiZm90Iiwic3ViIjoiOTkzYjczMTItOTJiOC00NDdiLTgzNzctY2NjOTE4NzllNTFkIiwidHlwIjoiQmVhcmVyIiwiYXpwIjoiczI0d2ViYXBwIiwic2Vzc2lvbl9zdGF0ZSI6ImEzMDI0MTM2LTQzOTgtNDgwMS04N2VkLWY4NzY5MjZjOWM1ZiIsInJlc291cmNlX2FjY2VzcyI6eyJmb3QiOnsicm9sZXMiOlsiVXNlciIsIkludmVzdG9yIl19fSwic2NvcGUiOiJ0cmFkZXIiLCJzaWQiOiJhMzAyNDEzNi00Mzk4LTQ4MDEtODdlZC1mODc2OTI2YzljNWYiLCJ0cmFkZUFjY291bnRzIjoiQUswOTA5IiwiYWNjRGVyaXZhdGl2ZSI6Ilt7XCJhY2NvdW50XCI6XCJBSzA5MDlEXCIsXCJzdGF0dXNcIjoxfV0iLCJhY2NNYXJnaW4iOiJbe1wiYWNjb3VudFwiOlwiQUswOTA5OFwiLFwic3RhdHVzXCI6MX1dIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWswOTA5IiwiYWNjQ2FzaCI6Ilt7XCJhY2NvdW50XCI6XCJBSzA5MDkxXCIsXCJzdGF0dXNcIjoxfV0ifQ.p78QIxY5gvLNkfeIvhp_hvFCjYvclmlli0DTjNW8pr0TDzzqtZ2VObSzNNEnDZ1zlMJmRbkBmHOpASEwoLnjlNS6ONKL_HZlI-SIMJPlrfbG3PtOgCFdXEesdlDNIJu_ypZb46RvSFrNEXWhem-eup72X_DqqaPii5xvdXHIRthWPerwgXpr5HupfaQlm7qb8zmfA-Co-J3HoJAmPTAhpEwnzVna6acFW-VPvTCgHiGR3iWz6KEPJuZll4wa7Mk-tKM96LeSjjH_nFEBDFvwMjNreBLoxFsEI65M1YEZ4invsxPJ3LQDNR9DHRGNE70-QAUMMRl--phSJ5cLWiC1IA"
    query = "phân tích kỹ thuật mã cổ phiếu HSG"

    events = call_chat_api(query, bearer_token=TOKEN)
    print(events)

    # Human-readable output
    for i, ev in enumerate(events, start=1):
        print(f"Event #{i}")
        print(f"  text      : {ev.text}")
        print(f"  eventType : {ev.eventType}")
        print(f"  chatId    : {ev.chatId}")
        print("-" * 40)

    time.sleep(10)