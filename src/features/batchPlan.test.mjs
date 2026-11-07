import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
const source = await readFile(
  new URL("./batchPlan.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { planBatch, isBatchBlockingFailure } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("unknown provider outcomes are excluded from both all mode and mass retry", () => {
  const uncertain = [
    "BATCH_OUTCOME_UNKNOWN",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_CONNECTION_ERROR",
    "PROVIDER_TIMEOUT",
    "EVALUATOR_ERROR",
    "WORKER_INTERRUPTED",
  ];
  const data = {
    conversations: uncertain.map((errorCode) => ({
      id: errorCode,
      evaluation: { status: "failed", errorCode },
    })),
    appeals: [],
  };
  for (const mode of ["all", "new"]) {
    const plan = planBatch(data, [], "workspace:", mode);
    assert.deepEqual(plan.candidates, []);
    assert.deepEqual(plan.failed, []);
    assert.equal(plan.selected.length, uncertain.length);
  }
});
test("global budget/provider errors pause the queue irrespective of retry hints", () => {
  for (const code of [
    "BUDGET_EXCEEDED",
    "BUDGET_STORAGE_ERROR",
    "BUDGET_CONFIGURATION",
    "BUDGET_HISTORY_UNKNOWN",
    "MODEL_NOT_ALLOWED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_TIMEOUT",
  ]) {
    for (const retryable of [false, true])
      assert.equal(
        isBatchBlockingFailure({
          status: "failed",
          errorCode: code,
          errorRetryable: retryable,
        }),
        true,
        code,
      );
  }
});
test("dialogue-specific failures do not stop unrelated dialogues", () => {
  for (const code of [
    "MODEL_OUTPUT_INVALID",
    "INPUT_CHANGED",
    "MODEL_REFUSAL",
    "EVALUATOR_ERROR",
  ])
    assert.equal(
      isBatchBlockingFailure({ status: "failed", errorCode: code }),
      false,
      code,
    );
  assert.equal(isBatchBlockingFailure(null), false);
  assert.equal(
    isBatchBlockingFailure({
      status: "needs_review",
      errorCode: "BUDGET_EXCEEDED",
    }),
    false,
  );
});
const statuses = [
  null,
  "completed",
  "needs_review",
  "failed",
  "failed_validation",
  "retryable",
  "queued",
  "running",
];
const data = {
  conversations: statuses.map((status, index) => ({
    id: String(index),
    evaluation: status ? { status } : null,
  })),
  appeals: [{ conversationId: "4" }],
};
const datasets = [
  {
    group: "CONTROL / KZ",
    conversationIds: ["0", "1", "2", "3", "4", "5", "6", "7"],
  },
];
const ids = (items) => items.map((item) => item.id);
test("new run includes only unevaluated conversations in the selected group", () => {
  assert.deepEqual(
    ids(planBatch(data, datasets, "group:CONTROL / KZ", "new").candidates),
    ["0"],
  );
});
test("retry includes errors, never needs_review, completed, queued or appealed", () => {
  assert.deepEqual(
    ids(planBatch(data, datasets, "group:CONTROL / KZ", "new").failed),
    ["3", "5"],
  );
});
test("re-evaluation excludes appealed and already running conversations", () => {
  assert.deepEqual(
    ids(planBatch(data, datasets, "group:CONTROL / KZ", "all").candidates),
    ["0", "1", "2", "3", "5"],
  );
});
test("unknown or empty group never expands into the whole workspace", () => {
  assert.deepEqual(
    planBatch(data, datasets, "group:missing", "all").selected,
    [],
  );
  assert.deepEqual(planBatch(data, datasets, "", "all").candidates, []);
});
test("group membership is exact and workspace requires explicit selection", () => {
  assert.deepEqual(
    ids(
      planBatch(
        data,
        [{ group: "partial", conversationIds: ["0", "3"] }],
        "group:partial",
        "all",
      ).selected,
    ),
    ["0", "3"],
  );
  assert.equal(
    planBatch(data, datasets, "workspace:", "all").selected.length,
    8,
  );
});
