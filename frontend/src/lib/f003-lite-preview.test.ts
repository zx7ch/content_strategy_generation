import assert from "node:assert/strict";
import test from "node:test";

test("Next exposes the canonical F003 preview flag as disabled by default", async () => {
  const original = process.env.F003_LITE_PREVIEW_ENABLED;
  delete process.env.F003_LITE_PREVIEW_ENABLED;
  try {
    const { default: config } = await import(
      `../../next.config.mjs?f003-default-${Date.now()}`
    );
    assert.equal(config.env?.F003_LITE_PREVIEW_ENABLED, "false");
  } finally {
    if (original === undefined) {
      delete process.env.F003_LITE_PREVIEW_ENABLED;
    } else {
      process.env.F003_LITE_PREVIEW_ENABLED = original;
    }
  }
});

test("Next exposes an explicitly enabled canonical F003 preview flag", async () => {
  const original = process.env.F003_LITE_PREVIEW_ENABLED;
  process.env.F003_LITE_PREVIEW_ENABLED = "true";
  try {
    const { default: config } = await import(
      `../../next.config.mjs?f003-enabled-${Date.now()}`
    );
    assert.equal(config.env?.F003_LITE_PREVIEW_ENABLED, "true");
  } finally {
    if (original === undefined) {
      delete process.env.F003_LITE_PREVIEW_ENABLED;
    } else {
      process.env.F003_LITE_PREVIEW_ENABLED = original;
    }
  }
});
