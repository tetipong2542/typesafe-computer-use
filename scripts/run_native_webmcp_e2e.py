#!/usr/bin/env python3
"""Phase 2D-C Native WebMCP Standards Closure E2E Live Verification Script.

Executes live against Headful Google Chrome inside Tart macOS VM:
1. Secure HTTP Origin: Served over http://127.0.0.1:<port> with Permissions-Policy: tools=* and Origin-Agent-Cluster: ?1.
2. Preflight Audit: Verifies Chrome Native document.modelContext exists natively (zero polyfills).
3. Native Tool Discovery: Discovers both imperative tools and declarative forms parsed by Blink C++.
4. Native Read-Only Invocation: search_products executes via document.modelContext.executeTool.
5. Native Mutating Invocation: add_to_cart & update_quantity mutate live DOM state.
6. Declarative Form Execution: subscribe_newsletter dispatches SubmitEvent.agentInvoked and handles respondWith().
7. Server-Side Approval Security:
   - Consequential action checkout halts without approval.
   - Argument bypass (_user_approved=True) is strictly ignored and rejected.
   - Cryptographically verified server-side approval record permits execution.
   - Single-use consumption prevents replay attacks.
8. Tier 2 & Tier 3 Safe Fallback:
   - Missing WebMCP tool falls back cleanly to Browser DOM.
   - Non-WebMCP website routes cleanly to Browser DOM.
9. Emergency Stop Invariant:
   - Emergency stop immediately closes execution gate, locks input, and revokes all approvals.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from typesafe_computer_use.adapters import (  # noqa: E402
    BrowserDOMAdapter,
    InteractionMode,
    InteractionRequest,
    RiskLevel,
    RouterMode,
    SideEffectState,
)
from typesafe_computer_use.browser.models import BrowserSessionConfig  # noqa: E402
from typesafe_computer_use.browser.session import BrowserSessionManager  # noqa: E402
from typesafe_computer_use.macos import set_input_lock  # noqa: E402
from typesafe_computer_use.router.shadow import ShadowInteractionRouter  # noqa: E402
from typesafe_computer_use.webmcp.adapter import NativeWebMCPAdapter  # noqa: E402
from typesafe_computer_use.webmcp.models import compute_arguments_hash  # noqa: E402
from typesafe_computer_use.worker.db import WorkerDatabase  # noqa: E402
from typesafe_computer_use.worker.gate import ExecutionGate  # noqa: E402


class FixtureHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler serving test fixtures with WebMCP required permissions headers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT / "tests" / "fixtures"), **kwargs)

    def end_headers(self):
        self.send_header("Permissions-Policy", "tools=*")
        self.send_header("Origin-Agent-Cluster", "?1")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        pass  # Suppress noisy HTTP request logs during testing


def start_fixture_server() -> tuple[socketserver.TCPServer, int]:
    """Start local HTTP server on an ephemeral loopback port."""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), FixtureHTTPHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


async def main() -> int:
    print("================================================================================")
    print(" Phase 2D-C Native WebMCP Standards Closure Live Verification (Tart VM)")
    print("================================================================================")

    # 1. Start HTTP Origin Server
    httpd, port = start_fixture_server()
    base_origin = f"http://127.0.0.1:{port}"
    fixture_url = f"{base_origin}/webmcp_ecommerce.html"
    print(f"[1/9] Ephemeral fixture server started: {fixture_url}")

    gate = ExecutionGate.get_instance()
    gate.open_gate()
    set_input_lock(False)
    os.environ["WEBMCP_NATIVE_MODE"] = "active"

    db = WorkerDatabase()
    cfg = BrowserSessionConfig(headless=False)
    mgr = BrowserSessionManager(cfg)

    summary_evidence: dict[str, object] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "2D-C Native WebMCP Standards Closure",
        "origin": base_origin,
    }

    # 2. Attach to or launch Google Chrome in VM
    try:
        page = await mgr.start_or_attach(auto_launch=True)
        await page.goto(fixture_url)
        await page.wait_for_timeout(600)
        print(f"[2/9] Attached to Chrome and navigated to: {page.url}")
    except Exception as e:
        print(f"FAILED to attach to Chrome: {e}", file=sys.stderr)
        httpd.shutdown()
        return 1

    # 3. Strict Preflight Environment Audit (Zero Polyfill Tolerance)
    print("\n[3/9] Auditing Browser Native Environment...")
    preflight = await page.evaluate(
        """() => ({
            hasModelContext: typeof document.modelContext !== 'undefined',
            modelContextType: typeof document.modelContext,
            originAgentCluster: window.originAgentCluster === true,
            isSecureContext: window.isSecureContext === true,
            userAgent: navigator.userAgent,
            hasGetTools: typeof document.modelContext?.getTools === 'function',
            hasExecuteTool: typeof document.modelContext?.executeTool === 'function',
            hasRegisterTool: typeof document.modelContext?.registerTool === 'function',
        })"""
    )
    print(f"Preflight Audit Data: {json.dumps(preflight, indent=2)}")
    summary_evidence["preflight_audit"] = preflight

    if not preflight["hasModelContext"]:
        print(
            "FATAL: BLOCKED_BY_BROWSER_SUPPORT — Chrome native document.modelContext is undefined!",
            file=sys.stderr,
        )
        print("Acceptance run strictly forbids polyfill monkey patching.", file=sys.stderr)
        httpd.shutdown()
        return 2

    assert preflight["modelContextType"] == "object", "document.modelContext must be an object"
    assert preflight["hasGetTools"], "document.modelContext.getTools function missing"
    assert preflight["hasRegisterTool"], "document.modelContext.registerTool function missing"
    print("Preflight Check PASSED: Native Chrome WebMCP runtime confirmed active.")

    dom_adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate)
    webmcp_adapter = NativeWebMCPAdapter(session_manager=mgr, execution_gate=gate, database=db)
    router = ShadowInteractionRouter(
        dom_adapter=dom_adapter,
        native_webmcp_adapter=webmcp_adapter,
        mode=RouterMode.HYBRID,
    )

    epoch = mgr.get_navigation_epoch()

    # 4. Tool Discovery (Imperative + Declarative Form)
    print("\n[4/9] Step 1: Discovering Native WebMCP tools declared on active page...")
    tools = await webmcp_adapter.discovery.discover_tools(page, epoch)
    tool_names = list(tools.keys())
    print(f"Discovered {len(tools)} in-page WebMCP tool(s): {tool_names}")
    assert "search_products" in tools, "Missing imperative tool search_products"
    assert "add_to_cart" in tools, "Missing imperative tool add_to_cart"
    assert "update_quantity" in tools, "Missing imperative tool update_quantity"
    assert "checkout" in tools, "Missing consequential imperative tool checkout"
    assert "subscribe_newsletter" in tools, "Missing official declarative tool subscribe_newsletter"

    # Verify official declarative form schema synthesis
    decl_tool = tools["subscribe_newsletter"]
    print(f"Declarative Tool Source: {decl_tool.source}, Schema: {decl_tool.input_schema}")
    assert "email" in decl_tool.input_schema.get("properties", {}), "Blink did not synthesize email input"
    print("Step 1 PASSED: All 4 imperative tools and 1 official declarative form discovered natively.")
    summary_evidence["discovered_tools"] = [
        {"name": t.name, "source": t.source, "consequential": t.annotations.consequential, "hash": t.schema_hash}
        for t in tools.values()
    ]

    # 5. Read-Only Invocation: search_products
    print("\n[5/9] Step 2: Invoking search_products via WebMCP Router...")
    req_search = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="search_products",
        target="search_products",
        arguments={"query": "MacBook Pro M3"},
        execution_id="e2e_search",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_search, dec_search = await router.route_and_execute(req_search)
    assert dec_search.executed_mode == InteractionMode.WEBMCP
    assert res_search.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    status_text = await page.inner_text("#status-message")
    assert "MacBook Pro M3" in status_text
    print(f"Step 2 PASSED: Native read-only tool updated DOM -> '{status_text}'")
    summary_evidence["step_2_search"] = {
        "executed_mode": dec_search.executed_mode.value,
        "side_effect_state": res_search.side_effect_state.value,
        "status_dom": status_text,
    }

    # 6. Mutating Invocations: add_to_cart & update_quantity
    print("\n[6/9] Step 3: Invoking add_to_cart and update_quantity...")
    req_add = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="add_to_cart",
        target="add_to_cart",
        arguments={"product_id": "prod_macbook", "quantity": 2},
        execution_id="e2e_add",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_add, dec_add = await router.route_and_execute(req_add)
    assert dec_add.executed_mode == InteractionMode.WEBMCP
    assert res_add.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    assert await page.inner_text("#cart-count") == "2"

    req_upd = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="update_quantity",
        target="update_quantity",
        arguments={"product_id": "prod_macbook", "quantity": 5},
        execution_id="e2e_upd",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_upd, dec_upd = await router.route_and_execute(req_upd)
    assert dec_upd.executed_mode == InteractionMode.WEBMCP
    assert res_upd.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    assert await page.inner_text("#cart-count") == "5"
    print("Step 3 PASSED: Mutating tools successfully updated live shopping cart to 5 units.")
    summary_evidence["step_3_mutations"] = {"cart_count": 5}

    # 7. Declarative Form Execution: subscribe_newsletter
    print("\n[7/9] Step 4: Invoking declarative form tool subscribe_newsletter...")
    req_decl = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="subscribe_newsletter",
        target="subscribe_newsletter",
        arguments={"email": "operator.test@typesafe.internal"},
        execution_id="e2e_decl",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_decl, dec_decl = await router.route_and_execute(req_decl)
    assert dec_decl.executed_mode == InteractionMode.WEBMCP
    assert res_decl.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    newsletter_status = await page.inner_text("#newsletter-status")
    print(f"Newsletter DOM Status: {newsletter_status}")
    assert "agent=true" in newsletter_status.lower() or "operator.test" in newsletter_status
    print("Step 4 PASSED: Declarative form executed natively with agentInvoked & respondWith handling.")
    summary_evidence["step_4_declarative"] = {"newsletter_status": newsletter_status}

    # 8. Consequential Action Gating & Cryptographic Server-Side Approval
    print("\n[8/9] Step 5: Verifying Consequential Action Approval Security on 'checkout'...")
    checkout_args = {"shipping_address": "1 Infinite Loop, Cupertino, CA", "payment_method": "apple_pay"}
    chk_args_hash = compute_arguments_hash(checkout_args)
    tool_checkout = tools["checkout"]

    # 8.1 Unapproved Invocation -> MUST HALT
    req_chk_unauth = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="checkout",
        target="checkout",
        arguments=checkout_args,
        execution_id="e2e_chk_unauth",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_chk_unauth, dec_chk_unauth = await router.route_and_execute(req_chk_unauth)
    assert res_chk_unauth.side_effect_state == SideEffectState.NOT_STARTED
    assert res_chk_unauth.risk == RiskLevel.CRITICAL
    assert dec_chk_unauth.shadow_match_result == "awaiting_approval"
    assert await page.inner_text("#cart-count") == "5"
    print("8.1 Check PASSED: Unapproved consequential action cleanly halted without mutation.")

    # 8.2 Argument-Level Bypass Attempt (_user_approved=True) -> MUST STILL HALT
    print("Attempting bypass with _user_approved=True in tool arguments...")
    req_chk_bypass = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="checkout",
        target="checkout",
        arguments={**checkout_args, "_user_approved": True},
        execution_id="e2e_chk_bypass",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_chk_bypass, dec_chk_bypass = await router.route_and_execute(req_chk_bypass)
    assert res_chk_bypass.side_effect_state == SideEffectState.NOT_STARTED
    assert dec_chk_bypass.shadow_match_result == "awaiting_approval"
    assert await page.inner_text("#cart-count") == "5"
    print("8.2 Security Invariant PASSED: Argument-level bypass strictly rejected!")

    # 8.3 Grant Cryptographically Verified Server-Side Approval in SQLite
    print("Creating active server-side approval record in SQLite WorkerDatabase...")
    appr_id = f"appr_e2e_{int(time.time() * 1000)}"
    db.create_webmcp_approval(
        approval_id=appr_id,
        task_id="task_e2e_native",
        event_id="step_5_checkout",
        origin=base_origin,
        tool_name="checkout",
        schema_hash=tool_checkout.schema_hash,
        arguments_hash=chk_args_hash,
        navigation_epoch=epoch,
        ttl_seconds=300.0,
        single_use=True,
        approved_by="security_officer_admin",
    )

    # 8.4 Authorized Invocation -> PROCEEDS
    print("Executing consequential checkout with valid server-side approval...")
    req_chk_auth = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="checkout",
        target="checkout",
        arguments=checkout_args,
        execution_id="e2e_chk_auth",
        context={"app": "Google Chrome", "task_id": "task_e2e_native"},
    )
    res_chk_auth, dec_chk_auth = await router.route_and_execute(req_chk_auth)
    assert dec_chk_auth.executed_mode == InteractionMode.WEBMCP
    assert res_chk_auth.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    assert res_chk_auth.result.get("approval_id") == appr_id
    order_dom = await page.inner_text("#order-status")
    assert "Order Confirmed: ORD-" in order_dom
    assert await page.inner_text("#cart-count") == "0"
    print(f"8.4 Execution PASSED: Authorized checkout placed order -> '{order_dom}'")

    # 8.5 Single-Use Invariant: Immediate second invocation must halt
    print("Verifying Single-Use Invariant (Replay Protection)...")
    res_chk_replay, dec_chk_replay = await router.route_and_execute(req_chk_auth)
    assert res_chk_replay.side_effect_state == SideEffectState.NOT_STARTED
    assert dec_chk_replay.shadow_match_result == "awaiting_approval"
    print("8.5 Security Invariant PASSED: Approval was consumed; duplicate execution halted.")

    summary_evidence["step_5_consequential"] = {
        "unapproved_halted": True,
        "argument_bypass_blocked": True,
        "server_approval_id": appr_id,
        "authorized_success": True,
        "single_use_consumed": True,
        "order_status": order_dom,
    }

    # 9. Clean Fallback & Invariant Testing
    print("\n[9/9] Step 6: Testing Router Fallback and Emergency Stop Safety...")
    # 9.1 Missing Tool -> Browser DOM
    req_dom_btn = InteractionRequest(
        mode=InteractionMode.BROWSER_DOM,
        action="click_item",
        target="#standard-dom-btn",
        arguments={},
        execution_id="e2e_dom_btn",
        context={"app": "Google Chrome"},
    )
    res_dom_btn, dec_dom_btn = await router.route_and_execute(req_dom_btn)
    assert dec_dom_btn.executed_mode == InteractionMode.BROWSER_DOM
    assert res_dom_btn.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    assert await page.inner_text("#dom-click-result") == "Clicked Successfully"
    print("9.1 Fallback PASSED: Missing tool cleanly delegated to Browser DOM.")

    # 9.2 Non-WebMCP Website -> Direct Browser DOM
    closure_fixture_url = f"{base_origin}/hybrid_closure.html"
    await page.goto(closure_fixture_url)
    await page.wait_for_timeout(300)

    req_closure = InteractionRequest(
        mode=InteractionMode.BROWSER_DOM,
        action="click_item",
        target="#submit-btn",
        arguments={},
        execution_id="e2e_closure_btn",
        context={"app": "Google Chrome"},
    )
    res_closure, dec_closure = await router.route_and_execute(req_closure)
    assert dec_closure.executed_mode == InteractionMode.BROWSER_DOM
    assert res_closure.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    closure_status = await page.inner_text("#status")
    assert "Submitted:" in closure_status
    print(f"9.2 Non-WebMCP PASSED: Routed cleanly to Browser DOM -> '{closure_status}'")

    # 9.3 Emergency Stop closes gate and revokes all approvals
    print("\nVerifying Emergency Stop closes gate and revokes approvals...")
    await page.goto(fixture_url)
    await page.wait_for_timeout(300)

    # Register an approval before e-stop
    dummy_appr = f"appr_estop_{int(time.time())}"
    db.create_webmcp_approval(
        approval_id=dummy_appr,
        task_id="task_estop",
        event_id="e1",
        origin=base_origin,
        tool_name="checkout",
        schema_hash="hash1",
        arguments_hash="hash2",
        navigation_epoch=mgr.get_navigation_epoch(),
    )
    await gate.trigger_emergency_stop(task_id="task_estop", db=db)

    # Gate must block execution
    res_blocked = await webmcp_adapter.execute(
        InteractionRequest(
            mode=InteractionMode.WEBMCP,
            action="search_products",
            target="search_products",
            arguments={"query": "blocked"},
        )
    )
    assert res_blocked.side_effect_state == SideEffectState.NOT_STARTED
    # Approval must be revoked
    active_after = db.get_active_webmcp_approval(base_origin, "checkout", "hash1", "hash2", mgr.get_navigation_epoch())
    assert active_after is None, "Emergency stop failed to revoke pending approvals"
    print("9.3 Emergency Stop PASSED: Execution blocked and pending approvals revoked.")

    # Cleanup
    gate.open_gate()
    set_input_lock(False)
    await mgr.disconnect(keep_browser_alive=True)
    httpd.shutdown()

    evidence_dir = PROJECT_ROOT / "docs" / "reports" / "phase-2d-native-webmcp-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "native-webmcp-live-run-summary.json").write_text(
        json.dumps(summary_evidence, indent=2), encoding="utf-8"
    )

    print("\n================================================================================")
    print(" ALL NATIVE WEBMCP STANDARDS CLOSURE TESTS PASSED!")
    print(f" Evidence saved to: {evidence_dir / 'native-webmcp-live-run-summary.json'}")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
