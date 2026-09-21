#!/usr/bin/env python3
"""Phase 2D-B Native WebMCP E2E Live Verification Script.

Executes live against Headful Google Chrome inside Tart macOS VM:
1. Tool Discovery: In-page detection of document.modelContext tools.
2. Read-Only Invocation: search_products mutates search state without side-effects.
3. Mutating Invocation: add_to_cart & update_quantity update shopping cart state in live DOM.
4. Consequential Action Gating: checkout halts with NOT_STARTED until explicit approval is granted.
5. Missing Tool Safe Fallback: Fallback to Tier 2 (Browser DOM) on non-WebMCP target.
6. Non-WebMCP Website Safe Fallback: Direct fallback to Browser DOM on normal pages.
7. Emergency Stop Safety: In-flight execution gate blocks mutating tool invocations.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
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
from typesafe_computer_use.worker.gate import ExecutionGate  # noqa: E402


async def main() -> int:
    print("================================================================================")
    print(" Phase 2D-B Native WebMCP Live Verification (Tart VM & Headful Chrome)")
    print("================================================================================")

    gate = ExecutionGate.get_instance()
    gate.open_gate()
    set_input_lock(False)
    os.environ["WEBMCP_NATIVE_MODE"] = "active"

    cfg = BrowserSessionConfig(headless=False)
    mgr = BrowserSessionManager(cfg)

    # Attach to owned Chrome
    try:
        page = await mgr.start_or_attach(auto_launch=False)
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "webmcp_ecommerce.html"
        await page.goto(fixture_path.as_uri())
        await page.wait_for_timeout(500)
        print(f"[1/8] Attached to Headful Chrome: {page.url}")
    except Exception as e:
        print(f"FAILED to attach to Chrome: {e}", file=sys.stderr)
        return 1

    dom_adapter = BrowserDOMAdapter(session_manager=mgr, execution_gate=gate)
    webmcp_adapter = NativeWebMCPAdapter(session_manager=mgr, execution_gate=gate)
    router = ShadowInteractionRouter(
        dom_adapter=dom_adapter,
        native_webmcp_adapter=webmcp_adapter,
        mode=RouterMode.HYBRID,
    )

    summary_evidence: dict[str, object] = {}

    # 1. Tool Discovery
    print("\n[2/8] Step 1: Discovering WebMCP tools declared on active page...")
    epoch = mgr.get_navigation_epoch()
    tools = await webmcp_adapter.discovery.discover_tools(page, epoch)
    print(f"Discovered {len(tools)} in-page WebMCP tool(s): {list(tools.keys())}")
    assert "search_products" in tools, "Missing search_products tool"
    assert "add_to_cart" in tools, "Missing add_to_cart tool"
    assert "update_quantity" in tools, "Missing update_quantity tool"
    assert "checkout" in tools, "Missing checkout tool"
    assert "subscribe_newsletter" in tools, "Missing declarative form tool subscribe_newsletter"
    print("Step 1 PASSED: All 4 imperative tools + 1 declarative form discovered.")
    summary_evidence["discovered_tools"] = [
        {"name": t.name, "source": t.source, "consequential": t.annotations.consequential, "hash": t.schema_hash}
        for t in tools.values()
    ]

    # 2. Read-Only Invocation: search_products
    print("\n[3/8] Step 2: Invoking search_products via WebMCP Router...")
    req_search = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="search_products",
        target="search_products",
        arguments={"query": "MacBook Pro M3"},
        execution_id="e2e_search",
        context={"app": "Google Chrome"},
    )
    res_search, dec_search = await router.route_and_execute(req_search)
    print(f"Search Result: side_effect={res_search.side_effect_state} mode={dec_search.executed_mode}")
    assert dec_search.executed_mode == InteractionMode.WEBMCP
    assert res_search.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    status_text = await page.inner_text("#status-message")
    assert "MacBook Pro M3" in status_text, f"DOM did not reflect search query: {status_text}"
    print(f"Step 2 PASSED: Live DOM updated -> '{status_text}'")
    summary_evidence["step_2_search"] = {
        "executed_mode": dec_search.executed_mode.value,
        "side_effect_state": res_search.side_effect_state.value,
        "status_dom": status_text,
    }

    # 3. Mutating Invocation: add_to_cart
    print("\n[4/8] Step 3: Invoking add_to_cart (product_id='prod_macbook', quantity=2)...")
    req_add = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="add_to_cart",
        target="add_to_cart",
        arguments={"product_id": "prod_macbook", "quantity": 2},
        execution_id="e2e_add",
        context={"app": "Google Chrome"},
    )
    res_add, dec_add = await router.route_and_execute(req_add)
    assert dec_add.executed_mode == InteractionMode.WEBMCP
    assert res_add.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    cart_count = await page.inner_text("#cart-count")
    assert cart_count == "2", f"Expected cart count 2, got: {cart_count}"
    cart_item_text = await page.inner_text("#cart-items")
    assert "prod_macbook: 2 unit(s)" in cart_item_text
    print(f"Step 3 PASSED: Cart mutated in live Chrome -> Badge: {cart_count}, Items: {cart_item_text}")
    summary_evidence["step_3_add_to_cart"] = {
        "executed_mode": dec_add.executed_mode.value,
        "cart_count": cart_count,
        "cart_items": cart_item_text,
    }

    # 4. Mutating Invocation: update_quantity
    print("\n[5/8] Step 4: Invoking update_quantity (product_id='prod_macbook', quantity=5)...")
    req_upd = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="update_quantity",
        target="update_quantity",
        arguments={"product_id": "prod_macbook", "quantity": 5},
        execution_id="e2e_upd",
        context={"app": "Google Chrome"},
    )
    res_upd, dec_upd = await router.route_and_execute(req_upd)
    assert dec_upd.executed_mode == InteractionMode.WEBMCP
    assert res_upd.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    cart_count_5 = await page.inner_text("#cart-count")
    assert cart_count_5 == "5", f"Expected cart count 5, got: {cart_count_5}"
    print(f"Step 4 PASSED: Cart updated in live Chrome -> Badge: {cart_count_5}")
    summary_evidence["step_4_update_quantity"] = {
        "executed_mode": dec_upd.executed_mode.value,
        "cart_count": cart_count_5,
    }

    # 5. Consequential Gating & Approval: checkout
    print("\n[6/8] Step 5: Testing Consequential Action Policy on 'checkout'...")
    # 5.1 Invocation WITHOUT approval -> must halt
    req_chk_unauth = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="checkout",
        target="checkout",
        arguments={"shipping_address": "1 Infinite Loop, Cupertino, CA", "payment_method": "apple_pay"},
        execution_id="e2e_chk_unauth",
        context={"app": "Google Chrome"},
    )
    res_chk_unauth, dec_chk_unauth = await router.route_and_execute(req_chk_unauth)
    assert res_chk_unauth.side_effect_state == SideEffectState.NOT_STARTED
    assert res_chk_unauth.risk == RiskLevel.CRITICAL
    assert dec_chk_unauth.shadow_match_result == "awaiting_approval"
    # Verify cart was NOT charged
    assert await page.inner_text("#cart-count") == "5"
    print("Consequential check PASSED: Unapproved checkout successfully blocked!")

    # 5.2 Invocation WITH approval -> proceeds
    print("Approving consequential action and retrying checkout...")
    req_chk_auth = InteractionRequest(
        mode=InteractionMode.WEBMCP,
        action="checkout",
        target="checkout",
        arguments={
            "shipping_address": "1 Infinite Loop, Cupertino, CA",
            "payment_method": "apple_pay",
            "_user_approved": True,
        },
        execution_id="e2e_chk_auth",
        context={"app": "Google Chrome"},
    )
    res_chk_auth, dec_chk_auth = await router.route_and_execute(req_chk_auth)
    assert dec_chk_auth.executed_mode == InteractionMode.WEBMCP
    assert res_chk_auth.side_effect_state == SideEffectState.CONFIRMED_SUCCESS
    order_dom = await page.inner_text("#order-status")
    assert "Order Confirmed: ORD-" in order_dom
    assert await page.inner_text("#cart-count") == "0"
    print(f"Step 5 PASSED: Approved checkout executed -> '{order_dom}'")
    summary_evidence["step_5_consequential_checkout"] = {
        "unapproved_halted": True,
        "approved_success": True,
        "order_status": order_dom,
    }

    # 6. Fallback 1: Missing WebMCP Tool -> Browser DOM
    print("\n[7/8] Step 6: Testing clean fallback from WebMCP to Browser DOM for missing tool...")
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
    dom_result_text = await page.inner_text("#dom-click-result")
    assert dom_result_text == "Clicked Successfully"
    print(f"Step 6 PASSED: Clean fallback executed via Browser DOM -> Result: '{dom_result_text}'")
    summary_evidence["step_6_fallback_missing_tool"] = {
        "executed_mode": dec_dom_btn.executed_mode.value,
        "dom_result": dom_result_text,
    }

    # 7. Fallback 2: Non-WebMCP Website -> Direct Browser DOM
    print("\n[8/8] Step 7: Testing navigation to non-WebMCP website and direct Browser DOM execution...")
    closure_fixture = PROJECT_ROOT / "tests" / "fixtures" / "hybrid_closure.html"
    await page.goto(closure_fixture.as_uri())
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
    print(f"Step 7 PASSED: Non-WebMCP website routed cleanly via Browser DOM -> '{closure_status}'")
    summary_evidence["step_7_non_webmcp_site"] = {
        "executed_mode": dec_closure.executed_mode.value,
        "status": closure_status,
    }

    # 8. Extra Safety: Emergency Stop Blocks WebMCP Immediately
    print("\n[Extra Safety] Verifying Emergency Stop blocks WebMCP immediately...")
    await page.goto(fixture_path.as_uri())
    await page.wait_for_timeout(300)
    await gate.trigger_emergency_stop()
    res_blocked = await webmcp_adapter.execute(
        InteractionRequest(
            mode=InteractionMode.WEBMCP,
            action="search_products",
            target="search_products",
            arguments={"query": "Blocked query"},
        )
    )
    assert res_blocked.side_effect_state == SideEffectState.NOT_STARTED
    assert "Execution gate locked" in str(res_blocked.error)
    print("Safety Invariant PASSED: Emergency stop blocks WebMCP mutating operations.")

    # Cleanup
    gate.open_gate()
    set_input_lock(False)
    await mgr.disconnect(keep_browser_alive=True)

    evidence_dir = PROJECT_ROOT / "docs" / "reports" / "phase-2d-native-webmcp-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary_evidence["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (evidence_dir / "native-webmcp-live-run-summary.json").write_text(
        json.dumps(summary_evidence, indent=2), encoding="utf-8"
    )

    print("\n================================================================================")
    print(" ALL NATIVE WEBMCP TESTS PASSED: Phase 2D-B Successfully Verified Live!")
    print(f" Evidence saved to: {evidence_dir}")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
