/**
 * @fileoverview Domain error classes for OpenMausBot TypeSafe Adapter.
 * Provides explicit, typed error boundaries for network, safety, and invariant failures.
 */

export class TypeSafeError extends Error {
  constructor(message: string, public readonly code: string = "TYPESAFE_ERROR") {
    super(message);
    this.name = "TypeSafeError";
  }
}

export class WorkerConnectionError extends TypeSafeError {
  constructor(message: string, public readonly cause?: unknown) {
    super(message, "WORKER_CONNECTION_ERROR");
    this.name = "WorkerConnectionError";
  }
}

export class WorkerAuthError extends TypeSafeError {
  constructor(message: string = "Invalid or missing WORKER_AUTH_TOKEN") {
    super(message, "WORKER_AUTH_ERROR");
    this.name = "WorkerAuthError";
  }
}

export class TaskNotFoundError extends TypeSafeError {
  constructor(public readonly taskId: string) {
    super(`Task '${taskId}' not found on TypeSafe worker`, "TASK_NOT_FOUND");
    this.name = "TaskNotFoundError";
  }
}

/**
 * Thrown when an interaction leaves the side-effect in an UNKNOWN state.
 * Under enterprise invariants, retry or fallback is STRICTLY forbidden.
 */
export class UnknownSideEffectError extends TypeSafeError {
  constructor(
    public readonly action: string,
    public readonly target: string,
    public readonly details: string
  ) {
    super(
      `Execution halted: action '${action}' on '${target}' entered SideEffectState.UNKNOWN: ${details}. Immediate review required.`,
      "UNKNOWN_SIDE_EFFECT_HALT"
    );
    this.name = "UnknownSideEffectError";
  }
}

export class GateLockedError extends TypeSafeError {
  constructor(message: string = "Execution gate is locked or locked by emergency stop") {
    super(message, "GATE_LOCKED_ERROR");
    this.name = "GateLockedError";
  }
}

export class ActionGatedError extends TypeSafeError {
  constructor(
    public readonly eventId: string,
    public readonly action: string,
    public readonly target: string,
    public readonly reason: string
  ) {
    super(
      `Action '${action}' on '${target}' is gated (Event ID: ${eventId}): ${reason}. Awaiting approval.`,
      "ACTION_GATED_AWAITING_APPROVAL"
    );
    this.name = "ActionGatedError";
  }
}

export class EmergencyStoppedError extends TypeSafeError {
  constructor(public readonly taskId: string) {
    super(
      `Task '${taskId}' was halted via Emergency Stop. Synthetic inputs locked.`,
      "EMERGENCY_STOPPED"
    );
    this.name = "EmergencyStoppedError";
  }
}
