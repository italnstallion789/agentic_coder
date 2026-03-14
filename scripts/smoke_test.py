#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class HttpResponse:
    status: int
    body: str


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[HttpResponse, dict[str, Any]]:
    body_bytes: bytes | None = None
    req_headers = dict(headers or {})
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url=url, method=method, data=body_bytes, headers=req_headers)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            raw_body = response.read().decode("utf-8")
            parsed = json.loads(raw_body) if raw_body else {}
            return HttpResponse(status=response.status, body=raw_body), parsed
    except urllib.error.HTTPError as error:
        raw_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {url}: {raw_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Request failed {url}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON response from {url}: {error}") from error


def request_text(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    request = urllib.request.Request(url=url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            raw_body = response.read().decode("utf-8")
            return HttpResponse(status=response.status, body=raw_body)
    except urllib.error.HTTPError as error:
        raw_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {url}: {raw_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Request failed {url}: {error}") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for_health(base_url: str, headers: dict[str, str], wait_seconds: int) -> None:
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _response, payload = request_json("GET", f"{base_url}/healthz", headers=headers)
            if payload.get("status") == "ok":
                return
            last_error = f"unexpected payload: {payload}"
        except Exception as error:  # noqa: BLE001 - smoke retries should be broad
            last_error = str(error)
        time.sleep(2)
    raise RuntimeError(f"healthz did not become ready within {wait_seconds}s: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agentic Coder smoke test")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8080"))
    parser.add_argument(
        "--target-repository",
        default=os.getenv("SMOKE_TARGET_REPOSITORY", "predictiv"),
    )
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--operator", default=os.getenv("SMOKE_OPERATOR", "smoke-suite"))
    parser.add_argument("--admin-token", default=os.getenv("API_ADMIN_TOKEN", ""))
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers: dict[str, str] = {"X-Operator": args.operator}
    if args.admin_token:
        headers["X-Admin-Token"] = args.admin_token

    print(f"[smoke] waiting for API at {base_url}")
    wait_for_health(base_url, headers, args.wait_seconds)

    print("[smoke] checking /healthz")
    _response, health = request_json("GET", f"{base_url}/healthz", headers=headers)
    require(health.get("status") == "ok", f"healthz status != ok: {health}")

    print("[smoke] checking /readyz")
    _response, ready = request_json("GET", f"{base_url}/readyz", headers=headers)
    require(ready.get("status") == "ready", f"readyz status != ready: {ready}")
    checks = ready.get("checks") or {}
    require(checks.get("database") is True, f"database check failed: {ready}")
    require(checks.get("redis") is True, f"redis check failed: {ready}")

    print("[smoke] checking /dashboard/data")
    _response, dashboard = request_json("GET", f"{base_url}/dashboard/data", headers=headers)
    require("tasks" in dashboard, f"dashboard payload missing tasks: {dashboard}")

    print("[smoke] checking /chat page")
    chat_page = request_text("GET", f"{base_url}/chat", headers=headers)
    require(chat_page.status == 200, f"chat page returned {chat_page.status}")
    require("Chat Control Plane" in chat_page.body, "chat page content missing expected marker")

    print("[smoke] creating chat session")
    _response, created = request_json(
        "POST",
        f"{base_url}/chat/sessions",
        headers=headers,
        payload={
            "title": "Smoke test session",
            "target_repository": args.target_repository,
        },
    )
    session = created.get("session") or {}
    session_id = session.get("session_id")
    require(bool(session_id), f"chat session id missing: {created}")

    print("[smoke] appending message")
    _response, message_result = request_json(
        "POST",
        f"{base_url}/chat/sessions/{session_id}/messages",
        headers=headers,
        payload={"role": "user", "content": "Smoke test message"},
    )
    message = message_result.get("message") or {}
    require(message.get("session_id") == session_id, f"message session mismatch: {message_result}")

    print("[smoke] reading messages")
    _response, messages = request_json(
        "GET",
        f"{base_url}/chat/sessions/{session_id}/messages?limit=10",
        headers=headers,
    )
    items = messages.get("messages") or []
    require(len(items) >= 1, f"expected at least one message: {messages}")

    print("[smoke] reading chat runs")
    _response, runs = request_json(
        "GET",
        f"{base_url}/chat/sessions/{session_id}/runs?limit=10",
        headers=headers,
    )
    require("tasks" in runs, f"runs payload missing tasks: {runs}")

    print("[smoke] preparing chat dispatch plan")
    _response, prepared = request_json(
        "POST",
        f"{base_url}/chat/sessions/{session_id}/prepare",
        headers=headers,
        payload={},
    )
    require(prepared.get("ok") is True, f"prepare payload missing ok=true: {prepared}")
    require(
        prepared.get("backend") in {"github_coding_agent", "local_pipeline"},
        f"unexpected chat backend in prepare response: {prepared}",
    )
    require(
        isinstance(prepared.get("summary"), str) and prepared.get("summary"),
        f"prepare response missing summary: {prepared}",
    )

    print("[smoke] all checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - smoke failure should return non-zero
        print(f"[smoke] FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error
