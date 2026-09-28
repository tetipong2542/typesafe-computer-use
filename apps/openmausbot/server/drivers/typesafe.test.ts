/**
 * @fileoverview Test suite for TypeSafe Driver integration in OpenMausBot.
 */

import { describe, expect, it } from "vitest";
import { TypeSafeDriver, TYPESAFE_MODELS } from "./typesafe.ts";

describe("TypeSafe Driver for OpenMausBot", () => {
  it("defines correct driver metadata and model catalog", () => {
    expect(TypeSafeDriver.driverKind).toBe("typesafeComputer");
    expect(TypeSafeDriver.metadata.displayName).toContain("TypeSafe");
    expect(TypeSafeDriver.models.default).toBe("typesafe-hybrid-auto");
    expect(TypeSafeDriver.models.options.length).toBe(4);
    expect(TypeSafeDriver.models.options.map((o) => o.id)).toContain("typesafe-hybrid-auto");
  });

  it("decodes configuration correctly with defaults", () => {
    const config = TypeSafeDriver.defaultConfig();
    expect(config.url).toBe("http://127.0.0.1:8000");
    expect(config.authToken).toBeDefined();
  });

  it("creates a provider instance and executes turn in simulation mode", async () => {
    const instance = await TypeSafeDriver.create({
      instanceId: "inst_test_typesafe",
      displayName: "TypeSafe Test",
      enabled: true,
      environment: {},
      config: TypeSafeDriver.defaultConfig(),
    });

    expect(instance.driverKind).toBe("typesafeComputer");
    expect(instance.models).toEqual(TYPESAFE_MODELS);

    const snapshot = await instance.snapshot();
    expect(snapshot.state).toBe("available");

    const events: any[] = [];
    const unsubscribe = instance.adapter.onEvent((e) => {
      events.push(e);
    });

    const result = await instance.adapter.sendTurn({
      threadId: "th_test_123",
      turnId: "turn_test_123",
      text: "Find Figma in the resource directory",
    });

    expect(result.turnId).toBeDefined();

    // Wait for async simulation execution
    await new Promise((r) => setTimeout(r, 2200));

    expect(events.some((e) => e.type === "turn.started")).toBe(true);
    expect(events.some((e) => e.type === "content.delta")).toBe(true);
    expect(events.some((e) => e.type === "turn.completed")).toBe(true);

    unsubscribe();
    await instance.dispose();
  });
});
