#!/usr/bin/env python3
"""
Test the backend /chat/stream endpoint directly.
Usage:
  python test_backend_chat.py [BACKEND_URL]
  BACKEND_URL defaults to http://localhost:8000/api/v1
"""
import json
import sys

import requests

BACKEND_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api/v1"
STREAM_URL = f"{BACKEND_URL}/chat/stream"

# Use the same token as chat_client.py - update this for your environment
TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJ5WENObDU5UVdjbFR3YmZCWnlmRmxLMlVDLTVKV1M1MjhJYVcwU0xfUTVzIn0.eyJleHAiOjE3NzA5NzM2ODIsImlhdCI6MTc3MDk3MzM4MiwianRpIjoiZjMzMjgwY2EtNjc4Ni00ZmU5LThjMDMtNDNkMDlkMjFiOWVkIiwiaXNzIjoiaHR0cHM6Ly9hY2N0cy5tYnMuY29tLnZuL2F1dGgvcmVhbG1zL3BlcmljbGVzIiwiYXVkIjoiZm90Iiwic3ViIjoiOTkzYjczMTItOTJiOC00NDdiLTgzNzctY2NjOTE4NzllNTFkIiwidHlwIjoiQmVhcmVyIiwiYXpwIjoiczI0d2ViYXBwIiwic2Vzc2lvbl9zdGF0ZSI6ImEzMDI0MTM2LTQzOTgtNDgwMS04N2VkLWY4NzY5MjZjOWM1ZiIsInJlc291cmNlX2FjY2VzcyI6eyJmb3QiOnsicm9sZXMiOlsiVXNlciIsIkludmVzdG9yIl19fSwic2NvcGUiOiJ0cmFkZXIiLCJzaWQiOiJhMzAyNDEzNi00Mzk4LTQ4MDEtODdlZC1mODc2OTI2YzljNWYiLCJ0cmFkZUFjY291bnRzIjoiQUswOTA5IiwiYWNjRGVyaXZhdGl2ZSI6Ilt7XCJhY2NvdW50XCI6XCJBSzA5MDlEXCIsXCJzdGF0dXNcIjoxfV0iLCJhY2NNYXJnaW4iOiJbe1wiYWNjb3VudFwiOlwiQUswOTA5OFwiLFwic3RhdHVzXCI6MX1dIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWswOTA5IiwiYWNjQ2FzaCI6Ilt7XCJhY2NvdW50XCI6XCJBSzA5MDkxXCIsXCJzdGF0dXNcIjoxfV0ifQ.jmhaOfOZDzxXzRuItunLt9IqTuo-GH2NMJvfLV_OCgJ9gKrwhAdrTtkhXmQLrXlzWeVcapX3jnhffxoYzROWvNT4fXqpTljGSFOSBlJPoEYQHapaF9o5wfLzJ6aPnhQI6MX7zNArZbDxb2D8PvTM7LQvhLE6D6ixGXi9vmX7VBk0pSrdZ2HhQ5tJBylqOBHWJCONUkik1imHYoXxIdLvcG4aaUVFkVaLW0aXxKp7Lm6_PmpAFKbHxs0o4EFzUQgzpsoMXUYHKmK_ivYQ225uZr-O6hEqVV-r1yYVItF-n1S82LfsMwEzksS9x657CLYGQGkYrKH3EEokSuOPf9chOA"


def main():
    print(f"Testing {STREAM_URL}")
    print("Sending request...")
    resp = requests.post(
        STREAM_URL,
        json={"query": "phân tích kỹ thuật mã cổ phiếu HSG", "bearer_token": TOKEN},
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=120,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:500])
        return 1
    count = 0
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            payload = line.replace("data:", "").strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
                if "error" in data:
                    print(f"ERROR: {data['error']}")
                    return 1
                if "status" in data and data.get("status") == "connecting":
                    print("Received: started")
                    continue
                count += 1
                text = data.get("text", "")
                if count <= 5 or data.get("eventType") == "chat.output_text.done":
                    print(f"Event #{count}: eventType={data.get('eventType')!r} text={text[:50]!r}...")
            except json.JSONDecodeError:
                pass
    print(f"\nTotal events received: {count}")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
