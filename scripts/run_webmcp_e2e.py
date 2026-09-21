#!/usr/bin/env python3
"""Phase 2D Live WebMCP E2E Verification Script.

Executes live against Headful Chrome inside macOS Tart VM:
1. Verifies browser_status (active page, cdp port, epoch).
2. Verifies browser_get_dom (sanitized DOM enclosed in untrusted delimiters).
3. Verifies browser_fill (populates #search-input with precision test text).
4. Verifies browser_click (clicks #submit-btn, mutating live DOM).
5. Verifies browser_verify (asserts #status updated via read-only check).
6. Verifies safety gate enforcement (emergency stop blocks MCP tools immediately).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from typesafe_computer_use.adapters import (  # noqa: E402
    BrowserDOMAdapter,
)
from typesafe_computer_use.browser.models import BrowserSessionConfig  # noqa: E402
from typesafe_computer_use.browser.session import BrowserSessionManager  # noqa: E402
from typesafe_computer_use.macos import set_input_lock  # noqa: E402
from typesafe_computer_use.mcp.server import create_webmcp_server  # noqa: E402
from typesafe_computer_use.worker.gate import ExecutionGate  # noqa: E402


def _extract_text(res: object) -> str:
    if hasattr(res, "content") and res.content:
        first = res.content[0]
        return getattr(first, "text", str(first))
    return str(res)


async def main() -> int:
    print("================================================================================")
    print(" Phase 2D WebMCP Live Browser Tools Verification")
    print("================================================================================")

    gate = ExecutionGate.get_instance()
    gate.open_gate()
    set_input_lock(False)
    cfg = BrowserSessionConfig(headless=False)
    mgr = BrowserSessionManager(cfg)

    # Attach to owned Chrome
    try:
        page = await mgr.start_or_attach(auto_launch=False)
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "hybrid_closure.html"
        await page.goto(fixture_path.as_uri())
        print(f"[1/6] Chrome attached to fixture: {page.url}")
    except Exception as e:
        print(f"FAILED to attach to Chrome: {e}", file=sys.stderr)
        return 1

    dom_adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate)
    auth_token = os.environ.get("WORKER_AUTH_TOKEN", "a0fc8f4a999ec1d0d87b3ff9cedfc6d296614bcd529b1589a1c02bfe3e9017e6")
    mcp_server = create_webmcp_server(session_manager=mgr, dom_adapter=dom_adapter, execution_gate=gate, auth_token=auth_token)

    # 0. Verify unauthenticated rejection
    print("\n[Auth Check] Verifying unauthenticated tool calls are rejected...")
    res_unauth = await mcp_server.call_tool("browser_status", {})
    unauth_text = _extract_text(res_unauth)
    assert "Authentication required" in unauth_text, f"Expected auth rejection, got: {unauth_text}"
    print("Universal Authentication PASSED: Unauthenticated calls safely rejected.")

    # 1. browser_status
    print("\n[2/6] Calling browser_status tool with auth...")
    res_status = await mcp_server.call_tool("browser_status", {"auth_token": auth_token})
    status_text = _extract_text(res_status)
    print(f"Status Output: {status_text}")
    assert "hybrid_closure.html" in status_text, "Active tab URL mismatch in browser_status"
    print("browser_status tool PASSED.")

    # 2. browser_get_dom
    print("\n[3/6] Calling browser_get_dom tool with auth...")
    res_dom = await mcp_server.call_tool("browser_get_dom", {"max_elements": 100, "auth_token": auth_token})
    dom_text = _extract_text(res_dom)
    assert "<untrusted_dom_content>" in dom_text, "Missing untrusted boundary in browser_get_dom"
    assert "Submit Query" in dom_text, "Missing interactive element in DOM extraction"
    print(f"Extracted sanitized DOM length: {len(dom_text)} characters.")
    print("browser_get_dom tool PASSED.")

    # 3. browser_fill
    print("\n[4/6] Calling browser_fill tool on #search-input with auth...")
    query_text = "WebMCP Automation Query 2026"
    res_fill = await mcp_server.call_tool("browser_fill", {"target": "#search-input", "text": query_text, "auth_token": auth_token})
    fill_text = _extract_text(res_fill)
    print(f"Fill Output: {fill_text}")
    assert "confirmed_success" in fill_text, "browser_fill did not achieve confirmed_success"
    print("browser_fill tool PASSED.")

    # 4. browser_click
    print("\n[5/6] Calling browser_click tool on #submit-btn with auth...")
    res_click = await mcp_server.call_tool("browser_click", {"target": "#submit-btn", "auth_token": auth_token})
    click_text = _extract_text(res_click)
    print(f"Click Output: {click_text}")
    assert "confirmed_success" in click_text, "browser_click did not achieve confirmed_success"
    print("browser_click tool PASSED.")

    # 5. browser_verify
    print("\n[6/6] Calling browser_verify tool on #status with auth...")
    res_verify = await mcp_server.call_tool(
        "browser_verify",
        {
            "condition": "text_contains",
            "target": "#status",
            "expected_value": f"Submitted: {query_text}",
            "auth_token": auth_token,
        },
    )
    verify_text = _extract_text(res_verify)
    print(f"Verify Output: {verify_text}")
    assert "true" in verify_text.lower() or query_text in verify_text, "Verification of mutated status failed"
    print("browser_verify tool PASSED: Mutation confirmed in live DOM via MCP!")

    # 6. Safety Gate Enforcement Check
    print("\n[Extra Safety] Verifying emergency stop blocks mutating tools immediately...")
    await gate.trigger_emergency_stop()
    res_blocked = await mcp_server.call_tool("browser_click", {"target": "#submit-btn", "auth_token": auth_token})
    blocked_text = _extract_text(res_blocked)
    assert "Execution gate locked" in blocked_text, "Emergency stop failed to block mutating tool"
    print("Safety Invariant PASSED: Mutating tools blocked by ExecutionGate.")

    # Cleanup gate
    gate.open_gate()
    set_input_lock(False)

    # Disconnect
    await mgr.disconnect(keep_browser_alive=True)

    evidence_dir = PROJECT_ROOT / "docs" / "reports" / "phase-2d-webmcp-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "browser_status": status_text,
        "browser_fill_result": fill_text,
        "browser_click_result": click_text,
        "browser_verify_result": verify_text,
        "execution_gate_enforced": True,
    }
    (evidence_dir / "webmcp-live-run-summary.json").write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

    print("\n================================================================================")
    print(" ALL WEBMCP TESTS PASSED: Phase 2D Verified Live Against Headful Chrome!")
    print(f" Evidence saved to: {evidence_dir}")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
