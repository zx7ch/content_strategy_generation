import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

test("Brief confirmation requires an explicit marketing goal before enabling confirmation", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost",
  });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLSelectElement: dom.window.HTMLSelectElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: dom.window.navigator,
  });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { ContentResearchIntentCard } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      React.createElement(ContentResearchIntentCard, {
        intent: {
          seed: "夏季凉感T恤",
          presearch: {
            attempt_id: "attempt_1",
            workflow_run_id: "run_1",
            brief_id: "brief_1",
            status: "completed",
            subject_confirmation: "确认夏季凉感T恤",
            competitor_tags: [],
            research_directions: [],
            direction_catalog: ["product_marketing"],
            custom_research_question: "",
            timeout_status: "none",
            fallback_used: false,
            subject_structure: {
              canonical_subject: "夏季凉感T恤",
              core_entities: [{ canonical_name: "T恤" }],
              research_intents: ["凉感"],
            },
            subject_structure_state: "confirmed",
            subject_structure_reason_codes: [],
          },
        },
        onConfirmed: () => undefined,
        onPresearchUpdated: () => undefined,
        onError: () => undefined,
      }),
    );
  });

  const buttonNamed = (name: string) =>
    [...container.querySelectorAll("button")].find((button) => button.textContent === name);
  const confirm = buttonNamed("确认并开始调研") as HTMLButtonElement;
  assert.ok(confirm);
  assert.equal(confirm.disabled, true);

  await act(async () => {
    buttonNamed("准确，继续")?.click();
    buttonNamed("产品营销")?.click();
  });
  assert.equal(confirm.disabled, true);

  const goalSelector = container.querySelector(
    'select[aria-label="产品营销目标"]',
  ) as HTMLSelectElement;
  await act(async () => {
    goalSelector.value = "content_seeding";
    goalSelector.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
  assert.equal(confirm.disabled, false);

  await act(async () => {
    root.unmount();
  });
  container.remove();
});
