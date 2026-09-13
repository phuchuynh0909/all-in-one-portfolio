#!/usr/bin/env python3
"""Discover and call the portfolio FastAPI API without exposing credentials."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import getpass
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

import requests
from requests.compat import quote, unquote, urlencode, urlparse, urlunparse

SKILL_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT = SKILL_DIR / "references" / "openapi.json"
DEFAULT_BASE_URL = "http://localhost:8000/api/v1"
SENSITIVE_PARTS = ("token", "password", "secret", "code_verifier")
SENSITIVE_KEYS = {"code"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOGIN_PATH = "/auth/login"


def _base_url() -> str:
    return os.getenv("PORTFOLIO_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

def _normalized_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _call_path(path: str) -> str:
    normalized = _normalized_path(path)
    base_path = urlparse(_base_url()).path.rstrip("/")
    if base_path and normalized == base_path:
        return "/"
    if base_path and normalized.startswith(f"{base_path}/"):
        return normalized[len(base_path) :]
    return normalized


def _openapi_url() -> str:
    configured = os.getenv("PORTFOLIO_OPENAPI_URL")
    if configured:
        return configured
    parts = urlparse(_base_url())
    return urlunparse((parts.scheme, parts.netloc, "/openapi.json", "", "", ""))


def _timeout() -> float:
    return float(os.getenv("PORTFOLIO_API_TIMEOUT", "60"))

def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in SENSITIVE_KEYS or any(part in normalized for part in SENSITIVE_PARTS)


def _redact(value: Any, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {name: _redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

def _safe_url(url: str) -> str:
    parts = urlparse(url)
    safe_query = []
    for item in parts.query.split("&"):
        name, separator, value = item.partition("=")
        if separator and _is_sensitive_key(unquote(name)):
            value = quote("<REDACTED>")
        safe_query.append(f"{name}{separator}{value}")
    return urlunparse(parts._replace(query="&".join(safe_query)))


def _decode_response(response: requests.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        return response.text


def _print_json(value: Any) -> None:
    print(json.dumps(_redact(value), indent=2, ensure_ascii=False, default=str))


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    session: requests.Session | None = None,
) -> Any:
    client = session or requests
    response = client.request(
        method,
        url,
        json=body,
        headers={"Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    return _decode_response(response)


def _load_spec(*, live_only: bool = False) -> dict[str, Any]:
    try:
        value = _request_json(_openapi_url())
        if not isinstance(value, dict) or "paths" not in value:
            raise ValueError("live OpenAPI response is not a schema")
        return value
    except (requests.RequestException, ValueError, OSError) as exc:
        if live_only or not SNAPSHOT.exists():
            raise SystemExit(f"Cannot load OpenAPI from {_safe_url(_openapi_url())}: {exc}") from None
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _operations(spec: dict[str, Any]):
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                yield method.upper(), path, operation


def _find_operation(
    spec: dict[str, Any], method: str, path: str
) -> tuple[str, dict[str, Any]]:
    normalized = _normalized_path(path)
    prefixes = [urlparse(_base_url()).path.rstrip("/"), "/api/v1"]
    candidates = [normalized]
    candidates.extend(
        f"{prefix}{normalized}"
        for prefix in prefixes
        if prefix and not normalized.startswith(f"{prefix}/")
    )
    for candidate in dict.fromkeys(candidates):
        operation = spec.get("paths", {}).get(candidate, {}).get(method.lower())
        if operation is not None:
            return candidate, operation
    raise SystemExit(f"Operation not found: {method} {path}")


def _schema_name(schema: dict[str, Any]) -> str | None:
    reference = schema.get("$ref")
    return reference.rsplit("/", 1)[-1] if isinstance(reference, str) else None


def _resolved_schema(spec: dict[str, Any], schema: dict[str, Any]) -> Any:
    name = _schema_name(schema)
    if not name:
        return schema
    return {"name": name, "schema": spec.get("components", {}).get("schemas", {}).get(name, {})}


def _operation_contract(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    operation_path, operation = _find_operation(spec, method, path)
    request_content = (operation.get("requestBody") or {}).get("content", {})
    request_schema = next(iter(request_content.values()), {}).get("schema")
    responses = {}
    for status, response in operation.get("responses", {}).items():
        content = response.get("content", {})
        schema = next(iter(content.values()), {}).get("schema")
        responses[status] = {
            "description": response.get("description"),
            "schema": _resolved_schema(spec, schema) if schema else None,
        }
    return {
        "method": method,
        "path": operation_path,
        "summary": operation.get("summary"),
        "description": operation.get("description"),
        "tags": operation.get("tags", []),
        "parameters": operation.get("parameters", []),
        "request": _resolved_schema(spec, request_schema) if request_schema else None,
        "responses": responses,
    }


def _login_token(session: requests.Session) -> str:
    username = os.getenv("PORTFOLIO_API_USERNAME")
    password = os.getenv("PORTFOLIO_API_PASSWORD")
    if username and not password and sys.stdin.isatty():
        password = getpass.getpass("Portfolio API password: ")
    if not username or not password:
        raise SystemExit(
            "Authentication required. Set both PORTFOLIO_API_USERNAME and "
            "PORTFOLIO_API_PASSWORD."
        )
    try:
        response = _request_json(
            f"{_base_url()}{LOGIN_PATH}",
            method="POST",
            body={"username": username, "password": password},
            session=session,
        )
    except requests.HTTPError as exc:
        error_response = exc.response
        error = _decode_response(error_response) if error_response is not None else str(exc)
        _print_json({"status": error_response.status_code if error_response is not None else None, "error": error})
        raise SystemExit(1) from None
    except requests.RequestException as exc:
        raise SystemExit(f"Login failed: {exc}") from None
    if not isinstance(response, dict) or not response.get("access_token"):
        raise SystemExit("Login succeeded without an access token")
    return str(response["access_token"])


def _pairs(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Expected KEY=VALUE, got: {value}")
        pairs.append(tuple(value.split("=", 1)))
    return pairs


def _body(args: argparse.Namespace) -> Any:
    try:
        if args.data is not None:
            return json.loads(args.data)
        if args.data_file is not None:
            return json.loads(Path(args.data_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"Cannot load request JSON: {exc}") from None
    return None

def _multipart(
    args: argparse.Namespace,
) -> tuple[list[tuple[str, str]], list[tuple[str, Path]], dict[str, Any]] | None:
    if not args.file and not args.form:
        return None
    if args.data is not None or args.data_file is not None:
        raise SystemExit("JSON and multipart fields cannot be combined")
    form_fields = _pairs(args.form)
    file_fields = [(name, Path(value)) for name, value in _pairs(args.file)]
    files = []
    for name, path in file_fields:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise SystemExit(f"Cannot read upload file {path}: {exc}") from None
        files.append({"field": name, "path": str(path), "bytes": size})
    return form_fields, file_fields, {"form": form_fields, "files": files}


def _call(args: argparse.Namespace) -> None:
    method = args.method.upper()
    path = _call_path(args.path)
    if method in WRITE_METHODS and not args.confirm_write:
        raise SystemExit(f"{method} requires --confirm-write")
    query = urlencode(_pairs(args.query), doseq=True)
    url = f"{_base_url()}{path}"
    if query:
        url = f"{url}?{query}"
    body = _body(args)
    multipart = _multipart(args)
    preview_body = multipart[2] if multipart else body
    if args.dry_run:
        _print_json({"method": method, "url": _safe_url(url), "body": preview_body, "stream": args.stream})
        return

    with requests.Session() as session:
        token = _login_token(session)
        headers = {
            "Accept": "text/event-stream" if args.stream else "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            with ExitStack() as stack:
                request_options: dict[str, Any] = {}
                if multipart:
                    form_fields, file_fields, _ = multipart
                    request_options["data"] = form_fields
                    request_options["files"] = [
                        (
                            name,
                            (
                                path.name,
                                stack.enter_context(path.open("rb")),
                                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                            ),
                        )
                        for name, path in file_fields
                    ]
                elif body is not None:
                    request_options["json"] = body
                with session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=_timeout(),
                    stream=args.stream,
                    **request_options,
                ) as response:
                    response.raise_for_status()
                    if args.stream:
                        for line in response.iter_lines(decode_unicode=True):
                            print(line, flush=True)
                        return
                    _print_json(_decode_response(response))
        except requests.HTTPError as exc:
            error_response = exc.response
            error = _decode_response(error_response) if error_response is not None else str(exc)
            _print_json({"status": error_response.status_code if error_response is not None else None, "error": error})
            raise SystemExit(1) from None
        except requests.RequestException as exc:
            raise SystemExit(f"API request failed: {exc}") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list OpenAPI operations")
    list_parser.add_argument("--query", default="", help="filter method, path, tag, or summary")

    show_parser = commands.add_parser("show", help="show one OpenAPI operation")
    show_parser.add_argument("method")
    show_parser.add_argument("path")

    call_parser = commands.add_parser("call", help="invoke an API operation")
    call_parser.add_argument("method")
    call_parser.add_argument("path", help="path relative to /api/v1")
    call_parser.add_argument("--query", action="append", default=[], metavar="KEY=VALUE")
    body_group = call_parser.add_mutually_exclusive_group()
    body_group.add_argument("--data", help="inline JSON request body")
    body_group.add_argument("--data-file", help="JSON request body file")
    call_parser.add_argument("--form", action="append", default=[], metavar="FIELD=VALUE")
    call_parser.add_argument("--file", action="append", default=[], metavar="FIELD=PATH")
    call_parser.add_argument("--stream", action="store_true", help="consume Server-Sent Events")
    call_parser.add_argument("--confirm-write", action="store_true")
    call_parser.add_argument("--dry-run", action="store_true")

    commands.add_parser("refresh", help="replace the OpenAPI snapshot from the live API")
    args = parser.parse_args()

    if args.command == "call":
        _call(args)
        return
    if args.command == "refresh":
        spec = _load_spec(live_only=True)
        SNAPSHOT.write_text(
            json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {SNAPSHOT} with {sum(1 for _ in _operations(spec))} operations")
        return

    spec = _load_spec()
    if args.command == "show":
        _print_json(_operation_contract(spec, args.method.upper(), args.path))
        return

    needle = args.query.casefold()
    operations = []
    for method, path, operation in _operations(spec):
        summary = operation.get("summary") or ""
        tags = operation.get("tags", [])
        haystack = " ".join((method, path, summary, *tags)).casefold()
        if needle and needle not in haystack:
            continue
        operations.append({"method": method, "path": path, "summary": summary, "tags": tags})
    _print_json({"count": len(operations), "operations": operations})


if __name__ == "__main__":
    main()
