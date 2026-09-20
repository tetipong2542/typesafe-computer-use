# ADR 001: Background Computer Worker Isolation Architecture & Browser Integration

## Status
Accepted (Code and Host Browser Verified; Guest Tart VM E2E Pending)

## Context
`typesafe-computer-use` operates as an autonomous computer-use agent on macOS. When executing GUI actions via `--act` (controlling mouse, keyboard, and windows), running directly on the primary desktop session disrupts human user activities and exposes sensitive personal credentials.

To solve this, we require:
1. **Host Isolation**: Separating the agent execution environment from the primary macOS desktop session using virtual machines.
2. **Browser Interaction Layer**: Enabling high-precision, low-latency, structured web interactions without sacrificing visual desktop grounding.

## Architectural Decisions

### 1. VM Isolation Runtime: Tart on Apple Silicon
* **Technology**: [Tart](https://tart.run) utilizes macOS Virtualization.framework for native hardware acceleration on Apple Silicon (`arm64`).
* **Base Image & Immutable Digest**:
  ```text
  ghcr.io/cirruslabs/macos-sonoma-base@sha256:e2ebdfc4d354b336fe00d729c11a8136a019b8046f41cc183a2fe51b85d18f49
  ```
  *Size*: ~22.5 GB compressed.
  *Note*: Image references must strictly pin this immutable digest to guarantee deterministic and reproducible environments.
* **Human Takeover & VNC Exposure**:
  * The agent runs inside the Guest VM.
  * Remote desktop control for human takeover uses macOS built-in Screen Sharing (VNC port 5900) bound to the VM's bridge/shared IP:
    `vnc://admin:admin@<VM_GUEST_IP>:5900`
  * Never binds VNC to host `127.0.0.1` unless port-forwarded, ensuring clear routing to the isolated VM session.

---

### 2. Browser Integration Architecture: Option B (Managed External Chrome + Loopback CDP)

We evaluated two architectural approaches for browser automation:
* **Option A**: In-Process Playwright Browser (tied directly to the worker Python process).
* **Option B (Selected)**: Managed External Google Chrome connected via Loopback CDP (`127.0.0.1`) using dynamic port allocation (`--remote-debugging-port=0`).

#### Nuanced Session Persistence Comparison
* **Option A (In-Process Playwright Context)**:
  * *Ephemeral context*: Session cookies and storage are discarded upon process exit.
  * *Persistent context*: Cookies and local storage written to disk survive.
  * *Critical Limitation*: **Running tabs, in-memory DOM state, pending network requests, navigation history, and uncommitted form fields are killed** when the worker process restarts or crashes.
* **Option B (Managed External Chrome + Loopback CDP)**:
  * *Process Decoupling*: Google Chrome runs as an independent OS process in its own process group. Worker process crashes or restarts do not kill the browser or close open tabs.
  * *Dynamic Reconnectability*: The Worker daemon can reconnect dynamically over loopback CDP to the existing browser session and resume tasks without state loss.
  * *System Chrome & Future WebMCP Support*: Uses native Google Chrome installed in macOS or Tart VM, enabling custom flags, enterprise extensions, and emerging W3C WebMCP APIs.
  * *Profile Isolation & Security*: Uses an isolated profile directory (`TypeSafeWorker/ChromeProfile`), preventing interference with personal human profiles in compliance with the Chrome Remote Debugging Policy.

#### Trade-off & Limitations
* **Protocol Fidelity**: Connecting over CDP via Playwright (`connect_over_cdp`) has lower protocol fidelity than native Playwright protocol in specific areas (e.g. fine-grained download events or specialized network interception). Explicit adapter compatibility tests are maintained.

---

### 3. Dynamic Port Allocation & Process Ownership Security
To prevent port collision and unauthorized access to existing browser instances:
1. **Dynamic Port**: Starts Chrome with `--remote-debugging-port=0`, letting the OS assign an ephemeral port read from `DevToolsActivePort`.
2. **Loopback Only**: Enforces `--remote-debugging-address=127.0.0.1`. Non-loopback addresses (`0.0.0.0`, LAN IPs) are rejected with `BrowserSecurityError`.
3. **Profile Lock**: Acquires an advisory file lock (`fcntl.flock`) on `.worker_profile.lock` to prevent concurrent worker instances from corrupting the profile.
4. **Ownership Record**: Atomically writes `browser_ownership.json` containing:
   * `worker_id`
   * `pid`
   * `process_start_time` (inspected via `ps` to prevent PID reuse attacks)
   * `executable` (must match Google Chrome or Chromium)
   * `profile_dir`
   * `cdp_host`
   * `cdp_port`
   * `browser_version`
   * `created_at`

---

### 4. Side-Effect Lifecycle & Execution Safety
Actions executed via `BrowserDOMAdapter` track granular side-effect lifecycle states:
* `NOT_STARTED`: Cancelled or aborted before CDP mutation dispatch.
* `DISPATCHING`: Preparing locator resolution prior to CDP call.
* `DISPATCHED`: Transmitted via CDP to the browser engine, awaiting completion.
* `CONFIRMED_SUCCESS`: Mutation confirmed and verified.
* `CONFIRMED_FAILURE`: Failed cleanly without persistent state mutation.
* `UNKNOWN`: Ambiguous outcome due to timeout, cancellation, or crash during/after dispatch.

**Invariants for `UNKNOWN`**:
* Automatic retries are **strictly forbidden**.
* Fallback to visual execution is **strictly forbidden**.
* If read-only state verification cannot prove completion, or if the action involves critical operations (`payment`, `send`, `publish`, `delete`, `transfer`, `checkout`), the task immediately transitions to `TaskState.AWAITING_REVIEW`.
