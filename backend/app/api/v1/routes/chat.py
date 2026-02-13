import base64
import json
import os
import re
import struct
import time
import uuid
from typing import Generator, List, Optional

import clickhouse_connect
import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.core.settings import settings
from app.utils.chat_protos import build_dynamic_messages


ChatRequest, ChatStreamEvent = build_dynamic_messages()

API_URL = "https://mkt-adv-api.mbs.com.vn/chat.v2.ChatbotCoreServiceV2/Chat"
REFRESH_URL = "https://accts.mbs.com.vn/webuaa/refreshToken"

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatStreamRequest(BaseModel):
    query: str = Field(..., min_length=1)
    bearer_token: str = Field(..., min_length=1)
    refresh_token: Optional[str] = None
    master_account: Optional[str] = "AK0909"
    code_verifier: Optional[str] = None
    device_id: Optional[str] = None


class SaveChatNoteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    message: str = Field(..., min_length=1)
    chat_id: Optional[str] = None


class ChatNoteItem(BaseModel):
    symbol: str
    message: str
    chat_id: Optional[str] = None
    created_at: str


class ChatNotesResponse(BaseModel):
    notes: List[ChatNoteItem]


def _get_token_exp(token: str) -> Optional[int]:
    """Decode JWT payload and return exp (expiration) claim. No signature verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        pad = (4 - len(payload_b64) % 4) % 4
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + ("=" * pad))
        payload = json.loads(payload_bytes.decode("utf-8"))
        return payload.get("exp")
    except Exception:
        return None


def _is_token_expired(token: str, buffer_seconds: int = 60) -> bool:
    """True if token is expired or will expire within buffer_seconds."""
    exp = _get_token_exp(token)
    if exp is None:
        return False
    return exp <= (time.time() + buffer_seconds)


def _build_browser_headers(bearer_token: str) -> dict:
    return {
        "Accept": "application/grpc-web-text",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/grpc-web-text",
        "Origin": "https://s24.mbs.com.vn",
        "Referer": "https://s24.mbs.com.vn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "X-Grpc-Web": "1",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "authorization": f"Bearer {bearer_token}",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "x-channel": "S24",
        "x-client-device-id": "85a219bdd24f0bee22135fae6ae9d492",
        "x-client-request-id": str(uuid.uuid4()),
        "x-master-account": "AK0909",
        "x-version": "v1.2.84",
    }


def _refresh_access_token(
    access_token: str,
    refresh_token: str,
    master_account: Optional[str] = None,
    code_verifier: Optional[str] = None,
    device_id: Optional[str] = None,
) -> dict:
    """Call refresh endpoint and return new tokens."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://s24.mbs.com.vn",
        "Referer": "https://s24.mbs.com.vn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Authorization": f"Bearer {access_token}",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "x-channel": "S24",
        "x-client-device-id": "85a219bdd24f0bee22135fae6ae9d492",
        "x-client-request-id": str(uuid.uuid4()),
        "x-master-account": master_account or "AK0909",
        "x-version": "v1.2.84",
    }
    data = {"refresh_token": refresh_token}
    if master_account:
        data["master_account"] = master_account
    if code_verifier:
        data["code_verifier"] = code_verifier
    if device_id:
        data["device_id"] = device_id

    resp = requests.post(REFRESH_URL, headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_grpc_web_text_body(message_bytes: bytes) -> str:
    flags = 0
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


def _parse_grpc_web_text_frames(data: bytes) -> Generator[bytes, None, None]:
    """
    Yield raw message bytes for each data frame from decoded grpc-web-text binary.
    Each binary frame: 1-byte flags + 4-byte length + payload. Skip trailers (0x80).
    """
    offset = 0
    while offset + 5 <= len(data):
        flags = data[offset]
        (msg_len,) = struct.unpack(">I", data[offset + 1 : offset + 5])
        if offset + 5 + msg_len > len(data):
            break
        msg_bytes = data[offset + 5 : offset + 5 + msg_len]
        offset += 5 + msg_len
        if flags & 0x80:
            continue  # trailers frame, skip
        yield msg_bytes


def _stream_decode_and_parse(
    chunk_iter: Generator[bytes, None, None],
) -> Generator[bytes, None, None]:
    """
    Stream grpc-web-text: buffer base64 chunks, decode on padding boundaries,
    yield message bytes as soon as complete frames are available.
    """
    buffer = ""
    for chunk in chunk_iter:
        buffer += chunk.decode("ascii", errors="replace").replace("\n", "").replace("\r", "")
        parts = re.split(r"=+", buffer)
        buffer = parts.pop() if parts else ""
        for p in parts:
            if not p:
                continue
            pad = (4 - len(p) % 4) % 4
            try:
                decoded = base64.b64decode(p + "=" * pad)
            except Exception:
                continue
            for msg_bytes in _parse_grpc_web_text_frames(decoded):
                yield msg_bytes


def _get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def _ensure_chat_notes_table(client, database: str, table: str) -> None:
    client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            id UUID DEFAULT generateUUIDv4(),
            symbol String,
            message String,
            chat_id Nullable(String),
            created_at DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = MergeTree
        ORDER BY (symbol, created_at)
        """
    )


@router.post("/notes")
def save_chat_note(request: SaveChatNoteRequest) -> dict:
    database = settings.clickhouse_db
    table = os.getenv("CLICKHOUSE_CHAT_NOTES_TABLE", "chat_agent_notes")
    client = _get_clickhouse_client()
    try:
        _ensure_chat_notes_table(client, database, table)
        client.insert(
            table=f"{database}.{table}",
            data=[[
                request.symbol.strip().upper(),
                request.message,
                request.chat_id,
            ]],
            column_names=["symbol", "message", "chat_id"],
        )
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("Failed to save chat note")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        client.close()


@router.get("/notes", response_model=ChatNotesResponse)
def list_chat_notes(symbol: str = Query(..., min_length=1, max_length=32), limit: int = Query(100, ge=1, le=500)) -> ChatNotesResponse:
    database = settings.clickhouse_db
    table = os.getenv("CLICKHOUSE_CHAT_NOTES_TABLE", "chat_agent_notes")
    client = _get_clickhouse_client()
    try:
        _ensure_chat_notes_table(client, database, table)
        query = (
            f"SELECT symbol, message, chat_id, created_at "
            f"FROM {database}.{table} "
            "WHERE symbol = %(symbol)s "
            "ORDER BY created_at DESC "
            "LIMIT %(limit)s"
        )
        result = client.query(query, parameters={"symbol": symbol.strip().upper(), "limit": limit})
        notes = [
            ChatNoteItem(
                symbol=row[0],
                message=row[1],
                chat_id=row[2],
                created_at=row[3].isoformat() if row[3] is not None else "",
            )
            for row in result.result_rows
        ]
        return ChatNotesResponse(notes=notes)
    except Exception as exc:
        logger.exception("Failed to list chat notes")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        client.close()


@router.post("/stream")
def stream_chat(request: ChatStreamRequest) -> StreamingResponse:
    def event_generator() -> Generator[str, None, None]:
        logger.info(
            "Chat stream start: query_len={} bearer_token_present={}",
            len(request.query),
            bool(request.bearer_token),
        )
        req = ChatRequest()
        req.query = request.query
        req_bytes = req.SerializeToString()
        payload = build_grpc_web_text_body(req_bytes)

        access_token = request.bearer_token
        can_refresh = bool(request.refresh_token)

        def do_chat_request(token: str):
            headers = _build_browser_headers(token)
            headers["Content-Type"] = "application/grpc-web-text"
            return requests.post(API_URL, data=payload, headers=headers, stream=True, timeout=120)

        try:
            yield f"event: started\ndata: {json.dumps({'status': 'connecting'})}\n\n"

            if _is_token_expired(access_token) and can_refresh:
                logger.info("Access token expired (from exp claim), refreshing")
                try:
                    refresh_result = _refresh_access_token(
                        access_token,
                        request.refresh_token,
                        request.master_account,
                        request.code_verifier,
                        request.device_id,
                    )
                    new_access = refresh_result.get("access_token")
                    new_refresh = refresh_result.get("refresh_token")
                    if new_access:
                        access_token = new_access
                        yield f"event: token_refreshed\ndata: {json.dumps({'access_token': new_access, 'refresh_token': new_refresh or request.refresh_token})}\n\n"
                    else:
                        raise ValueError("Refresh response missing access_token")
                except Exception as refresh_exc:
                    logger.exception("Token refresh failed")
                    yield f"event: error\ndata: {json.dumps({'error': f'Token expired and refresh failed: {refresh_exc}'})}\n\n"
                    return

            resp = do_chat_request(access_token)
            with resp:
                logger.info("Chat stream response: status={}", resp.status_code)
                resp.raise_for_status()
                # Stream: process chunks as they arrive, yield events immediately
                def chunk_iter():
                    for chunk in resp.iter_content(chunk_size=4096):
                        if chunk:
                            yield chunk

                for msg_bytes in _stream_decode_and_parse(chunk_iter()):
                    try:
                        event = ChatStreamEvent()
                        event.ParseFromString(msg_bytes)
                        data = {
                            "text": event.text,
                            "eventType": event.eventType,
                            "chatId": event.chatId,
                        }
                        if event.eventType == "chat.output_text.done":
                            # skip the last message
                            break
                        yield f"event: message\ndata: {json.dumps(data)}\n\n"
                    except Exception:
                        logger.exception("Chat stream decode failed")
                        continue
        except requests.RequestException as exc:
            logger.exception("Chat stream request failed")
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
