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

test("Next honors an isolated build directory for browser test processes", async () => {
  const original = process.env.NEXT_DIST_DIR;
  process.env.NEXT_DIST_DIR = ".next-test-f003";
  try {
    const { default: config } = await import(
      `../../next.config.mjs?f003-dist-dir-${Date.now()}`
    );
    assert.equal(config.distDir, ".next-test-f003");
  } finally {
    if (original === undefined) {
      delete process.env.NEXT_DIST_DIR;
    } else {
      process.env.NEXT_DIST_DIR = original;
    }
  }
});

for (const enabledValue of ["TRUE", "1", "yes", "on"]) {
  test(`Next normalizes backend-supported ${enabledValue} to an enabled client flag`, async () => {
    const original = process.env.F003_LITE_PREVIEW_ENABLED;
    process.env.F003_LITE_PREVIEW_ENABLED = enabledValue;
    try {
      const { default: config } = await import(
        `../../next.config.mjs?f003-enabled-${enabledValue}-${Date.now()}`
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
}

for (const disabledValue of ["FALSE", "0", "no", "off"]) {
  test(`Next normalizes backend-supported ${disabledValue} to a disabled client flag`, async () => {
    const original = process.env.F003_LITE_PREVIEW_ENABLED;
    process.env.F003_LITE_PREVIEW_ENABLED = disabledValue;
    try {
      const { default: config } = await import(
        `../../next.config.mjs?f003-disabled-${disabledValue}-${Date.now()}`
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
}
