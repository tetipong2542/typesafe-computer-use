"""Run the pinned upstream agent over newline-delimited CDP/provider RPC."""

import json
import os
import re
import sys
import time
import types
from pathlib import Path


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def request(kind, **payload):
    request.counter += 1
    send({"type": kind, "id": request.counter, **payload})
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("Node adapter closed the RPC channel")
    response = json.loads(line)
    if response.get("id") != request.counter:
        raise RuntimeError("Node adapter returned an out-of-order RPC response")
    if response.get("error") is not None:
        raise RuntimeError(response["error"])
    return response.get("result")


request.counter = 0


def audit_snapshot(agent):
    snapshot = agent.snapshot()
    snapshot['page'] = {key: value for key, value in snapshot['page'].items() if key not in {'guards', 'page_key', 'marker'}}
    snapshot['decisions'] = [{key: value for key, value in decision.items() if key != 'request'} for decision in snapshot['decisions']]
    if snapshot.get('decision'):
        snapshot['decision'] = {key: value for key, value in snapshot['decision'].items() if key != 'request'}
    return snapshot


def install_browser_harness_stub():
    package = types.ModuleType("browser_harness")
    package.__path__ = []
    admin = types.ModuleType("browser_harness.admin")
    helpers = types.ModuleType("browser_harness.helpers")
    admin.ensure_daemon = lambda: None
    helpers.cdp = lambda method, session_id=None, **params: request("rpc", method=method, params=params)
    sys.modules.update(
        {
            "browser_harness": package,
            "browser_harness.admin": admin,
            "browser_harness.helpers": helpers,
        }
    )


def main():
    config = json.loads(sys.stdin.readline())
    if config.get("type") != "init":
        raise ValueError("Expected init message")

    install_browser_harness_stub()
    from jev_ultrafast import agent as agent_module
    from jev_ultrafast import browser as browser_module
    from jev_ultrafast import model as model_module

    # Generic authorized-login support; upstream source stays unmodified.
    browser_module.READ_STATE = Path(__file__).with_name('snapshot-windtunnel.js').read_text()
    browser_module.MARKER = f"(() => {{ const state={browser_module.READ_STATE}; return state?.marker ?? null; }})()"

    limit = int(config["stepBudget"])
    if limit < 1:
        raise ValueError("stepBudget must be positive")
    agent_module.MAX_STEPS = limit

    def owned_browser(self, _url):
        self.target = None
        self.session = "windtunnel-owned"
        self.after_input = None

    browser_module.Browser.__init__ = owned_browser
    browser_module.Browser.close = lambda _self: None
    model_module.NEXT_ACTION = Path(__file__).with_name('policy.txt').read_text().strip()
    terminal_descriptions = json.loads(Path(__file__).with_name('policy-overrides.json').read_text())

    def post_json(url, _key, body):
        if url == "https://api.typesafe.ai/v1/systemone":
            body["questions"]["operation"]["criteria"].update(terminal_descriptions)
        return request("provider", url=url, body=body)

    model_module.post_json = post_json
    original_action_space = model_module.action_space

    def action_space_with_omissions(actions):
        elements, targets, controls = original_action_space(actions)
        for target, action in targets.get("SELECT", {}).items():
            elements[int(target.split(":")[0]) - 1]["options_omitted"] = action.get("options_omitted", 0)
        return elements, targets, controls

    model_module.action_space = action_space_with_omissions
    agent_module.action_space = action_space_with_omissions

    original_fresh = browser_module.Browser.fresh
    original_command = agent_module.Agent.command

    def command_with_fill_scope(self, name, body=None):
        browser = self.state["browser"]
        previous = getattr(browser, "_fill_freshness_scope", None)
        previous_terminal = getattr(browser, "_terminal_freshness_scope", None)
        if name == "act":
            choice = (self.state.get("decision") or {}).get("choice")
            browser._terminal_freshness_scope = choice if choice in {"DONE", "BLOCKED"} else None
            selected = next((a for a in self.state["page"]["actions"] if a["id"] == choice), None)
            browser._fill_freshness_scope = selected if selected and selected["kind"] == "fill" else None
        try:
            return original_command(self, name, body)
        finally:
            browser._fill_freshness_scope = previous
            browser._terminal_freshness_scope = previous_terminal

    def audited_fresh(self, page, action=None):
        selected = action or getattr(self, "_fill_freshness_scope", None)
        scoped_fill = selected is not None and selected["kind"] == "fill"
        checked_action = {**selected, "kind": "click"} if scoped_fill else action
        scope = "fill-pre-input" if scoped_fill and action else "fill-pre-helper" if scoped_fill else (action or {}).get("kind", "global")
        changed_components = []
        try:
            if checked_action is not None and checked_action["kind"] in {"click", "select"}:
                fresh = original_fresh(self, page, checked_action)
            else:
                marker = self.evaluate(browser_module.MARKER)
                fresh = marker == page["marker"]
                if not fresh:
                    old = page["marker"]
                    groups = {"time_origin": [0], "url": [1], "scroll": [2, 3], "viewport": [4, 5], "title": [6], "text": [7], "controls": [8], "inputs": [9]}
                    changed_components = [name for name, positions in groups.items() if marker is None or any(i >= len(marker) or marker[i] != old[i] for i in positions)]
        except browser_module.StalePage as error:
            send({"type": "event", "event": {"kind": "stale_rejection", "stage": "fresh", "scope": scope, "reason": str(error)}})
            raise
        if not fresh:
            send({"type": "event", "event": {"kind": "stale_rejection", "stage": "fresh", "scope": scope, "reason": "Observed page or target guard changed", "changed_components": changed_components, "terminal": getattr(self, "_terminal_freshness_scope", None)}})
        return fresh

    agent_module.Agent.command = command_with_fill_scope
    browser_module.Browser.fresh = audited_fresh

    current = {"agent": None}
    original_choose = agent_module.choose

    def observed_choose(state, goal, history):
        send(
            {
                "type": "event",
                "event": {
                    "kind": "snapshot",
                    "phase": "before_model",
                    "snapshot": audit_snapshot(current["agent"]),
                },
            }
        )
        decision = original_choose(state, goal, history)
        send({"type": "event", "event": {"kind": "decision", "decision": {key: value for key, value in decision.items() if key != 'request'}}})
        return decision

    agent_module.choose = observed_choose
    original_act = browser_module.Browser.act
    original_observe = browser_module.Browser.observe

    def observed_after_action(self, screenshot=True):
        pending = getattr(self, "_post_action_observation", None)
        if pending is None:
            return original_observe(self, screenshot=screenshot)
        self._post_action_observation = None
        baseline, completed_at = pending
        deadline = completed_at + 1.5
        initial_fingerprint = final_fingerprint = previous = None
        page = None
        changed = False
        poll_count = stale_reads = stable_samples = 0
        productive_controls = 0
        outcome = "unchanged-deadline"
        try:
            while True:
                try:
                    page = original_observe(self, screenshot=screenshot) if poll_count == 0 else browser_module.browser_operation({"operation": "observe", "session": self.session, "screenshot": screenshot})
                    final_fingerprint = page["fingerprint"]
                    if initial_fingerprint is None:
                        initial_fingerprint = final_fingerprint
                    changed = changed or final_fingerprint != baseline
                    stable_samples = stable_samples + 1 if previous == final_fingerprint else 1
                    previous = final_fingerprint
                    productive_controls = sum(a["kind"] in {"click", "fill", "select"} for a in page["actions"])
                    if changed and stable_samples >= 2 and productive_controls:
                        outcome = "stable"
                        break
                except Exception as error:
                    if not isinstance(error, browser_module.StalePage) and not re.search(r"Execution context was destroyed|Cannot find context|Inspected target navigated", str(error)):
                        raise
                    stale_reads += 1
                    stable_samples = 0
                    previous = None
                    send({"type": "event", "event": {"kind": "stale_rejection", "stage": "observe", "scope": "post-action", "reason": str(error)}})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.05, remaining))
                if time.monotonic() >= deadline:
                    break
                poll_count += 1
            if outcome != "stable":
                # Final read keeps the upstream navigation retry behavior; no mutation is replayed.
                page = original_observe(self, screenshot=screenshot)
                final_fingerprint = page["fingerprint"]
                changed = changed or final_fingerprint != baseline
                productive_controls = sum(a["kind"] in {"click", "fill", "select"} for a in page["actions"])
                outcome = "changed-deadline" if changed else "unchanged-deadline"
            return page
        except Exception as error:
            outcome = "error"
            raise
        finally:
            send({"type": "event", "event": {
                "kind": "post_action_observation", "baseline_fingerprint": baseline,
                "initial_fingerprint": initial_fingerprint, "final_fingerprint": final_fingerprint,
                "waited": poll_count > 0, "poll_count": poll_count, "stale_reads": stale_reads,
                "stable_samples": stable_samples, "productive_controls": productive_controls,
                "elapsed_ms": round((time.monotonic() - completed_at) * 1000), "outcome": outcome,
            }})

    browser_module.Browser.observe = observed_after_action

    def audited_act(self, action, page, text=None):
        try:
            result = original_act(self, action, page, text=text)
        except browser_module.StalePage as error:
            send({"type": "event", "event": {
                "kind": "stale_rejection", "reason": str(error), "action": action,
            }})
            raise
        self._post_action_observation = (page["fingerprint"], time.monotonic())
        audit_result = dict(result or {})
        if action["kind"] == "fill":
            try:
                audit_result["valueAfter"] = self.evaluate(
                    "(id=>{const e=window.__jevFast?.nodes.get(id);"
                    "if(!e?.isConnected)throw new Error('filled target detached before read-back');"
                    "return e.type==='password'?(e.value?'[filled password]':''):e.isContentEditable?e.innerText:e.value})(" + str(action["node"]) + ")"
                )
            except Exception as error:  # Read-back is audit only; never replay a mutation.
                audit_result["valueAfterError"] = str(error)
        send(
            {
                "type": "event",
                "event": {
                    "kind": "action",
                    "action": action,
                    "text": text,
                    "result": audit_result,
                },
            }
        )
        return result

    browser_module.Browser.act = audited_act

    agent = agent_module.Agent(config["url"], config["taskPrompt"], screenshots=False)
    current["agent"] = agent
    send(
        {
            "type": "event",
            "event": {"kind": "snapshot", "phase": "initial_observation", "snapshot": audit_snapshot(agent)},
        }
    )

    emitted_history = 0
    while agent.state["status"] not in {"done", "blocked"} and len(agent.state["decisions"]) < limit:
        agent.command("tick")
        while emitted_history < len(agent.state["history"]):
            send(
                {
                    "type": "event",
                    "event": {"kind": "history", "entry": agent.state["history"][emitted_history]},
                }
            )
            emitted_history += 1
        send(
            {
                "type": "event",
                "event": {"kind": "snapshot", "phase": "after_tick", "snapshot": audit_snapshot(agent)},
            }
        )

    snapshot = audit_snapshot(agent)
    status = agent.state["status"] if agent.state["status"] in {"done", "blocked"} else "budget_exhausted"
    send(
        {
            "type": "event",
            "event": {"kind": "final_state", "status": status, "snapshot": snapshot},
        }
    )
    send(
        {
            "type": "result",
            "status": status,
            "steps": len(agent.state["decisions"]),
            "actions": len(agent.state["history"]),
            "snapshot": snapshot,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        send({"type": "result", "status": "error", "failure": str(error)})
