/**
 * @fileoverview Type definitions and API contracts for OpenMausBot TypeSafe Adapter.
 * Defines shared interfaces conforming to TypeSafe Worker and Hybrid Router specs.
 */

export type InteractionMode = "webmcp" | "browser_dom" | "visual_grounded";

export type SideEffectState =
  | "not_started"
  | "in_progress"
  | "confirmed_success"
  | "confirmed_failure"
  | "unknown";

export type TaskState =
  | "queued"
  | "running"
  | "paused"
  | "awaiting_review"
  | "completed"
  | "failed"
  | "emergency_stopped";

export interface WorkerClientConfig {
  /** Worker API base URL (default: http://192.168.64.3:8000 or http://127.0.0.1:8000) */
  baseUrl?: string;
  /** Secret Bearer token for worker authentication */
  authToken?: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeoutMs?: number;
  /** VNC Screen Sharing host (optional) */
  vncHost?: string;
}

export interface CreateTaskOptions {
  /** High-level user goal or instructions */
  goal: string;
  /** Execution mode: true to execute actions, false for dry-run/preview */
  act?: boolean;
  /** Maximum number of execution steps */
  steps?: number;
  /** Minimum confidence threshold for computer use policy (0.0 to 1.0) */
  minConfidence?: number;
  /** Delay between interaction steps in seconds */
  delay?: number;
  /** Browser session profile override (e.g. "default", "chrome-native-webmcp") */
  browser?: string;
}

export interface TaskRecord {
  taskId: string;
  runId: string;
  state: TaskState;
  goal: string;
  createdAt: string;
  updatedAt?: string;
  error?: string | null;
  sideEffectState?: SideEffectState;
  navigationEpoch?: number;
  stepsExecuted?: number;
  selectedMode?: InteractionMode;
  executedMode?: InteractionMode;
  approvalRequired?: {
    eventId: string;
    action: string;
    target: string;
    screenshotHash?: string;
    actionFingerprint?: string;
  } | null;
}

export interface TaskStreamEvent {
  type: "status" | "step" | "log" | "action_gated" | "completed" | "error" | "ping";
  taskId: string;
  timestamp: number;
  data: Record<string, unknown>;
}

export interface ApprovalPayload {
  step: number;
  action: string;
  target: string;
  screenshotHash?: string;
  actionFingerprint?: string;
}

export interface WebMCPApprovalPayload {
  eventId?: string;
  origin: string;
  toolName: string;
  schemaHash: string;
  argumentsHash: string;
  navigationEpoch: number;
  ttlSeconds?: number;
  singleUse?: boolean;
  approvedBy?: string;
}

export interface BrowserToolResult<T = unknown> {
  success: boolean;
  data?: T;
  error?: string | null;
  sideEffectState: SideEffectState;
  navigationEpoch?: number;
  screenshotBase64?: string | null;
}

export interface BrowserStatusData {
  url: string;
  title: string;
  navigationEpoch: number;
  activeTabId?: string;
  ready: boolean;
}

export interface OrchestratorPlan {
  taskId: string;
  goal: string;
  preferredMode: InteractionMode;
  fallbackAllowed: boolean;
}

export interface OrchestrationResult {
  taskId: string;
  goal: string;
  success: boolean;
  finalAnswer?: string;
  executedMode: InteractionMode;
  fallbackHistory: Array<{
    from: InteractionMode;
    to: InteractionMode;
    reason: string;
    consecutiveStalls?: number;
  }>;
  turns: number;
  totalTokens: number;
  actualCostUsd: number;
  error?: string | null;
}
