import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// Exercise the production TS adapters without adding a frontend test dependency.
async function moduleUrl(file, replacements = {}) {
  let source = await readFile(new URL(file, import.meta.url), "utf8");
  for (const [path, replacement] of Object.entries(replacements))
    source = source.replaceAll(`"${path}"`, JSON.stringify(replacement));
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
}
const decimalUrl = await moduleUrl("./decimal.ts");
const modelsUrl = await moduleUrl("./viewmodels.ts", {
  "./decimal": decimalUrl,
});
const apiUrl = await moduleUrl("./api.ts", {
  "./decimal": decimalUrl,
  "./viewmodels": modelsUrl,
});
const decimal = await import(decimalUrl);
const models = await import(modelsUrl);
const api = await import(apiUrl);
const criticalStatus = await import(
  await moduleUrl("../features/criticalStatus.ts")
);
const timing = await import(await moduleUrl("../features/audioTiming.ts"));

test("audio positions handle ISO zones, offsets and invalid timestamps", () => {
  assert.equal(timing.audioPosition("2026-11-07T10:00:05+05:00", "2026-11-07T05:02:22Z", 3), 140);
  assert.equal(timing.audioPosition("00:05", "00:20", -2), 13);
  assert.equal(timing.audioPosition("00:05", "00:20", -30), 0);
  assert.equal(timing.audioPosition("00:20", "00:05", 0), null);
  assert.equal(timing.audioPosition("bad", "00:05", 0), null);
  assert.equal(timing.audioPosition("00:00", "2026-11-07T05:02:22Z", 0), null);
  assert.equal(timing.audioPosition("00:00", "00:01", NaN), null);
});

const checklist = {
  schema_version: "2.0",
  id: "rubric",
  name: "Стандарт",
  version: 1,
  criteria: [
    {
      id: "greeting",
      title: "Приветствие",
      description: "Поздороваться",
      section: "Начало",
      type: "yes_no",
      weight: "0.125",
      max: "1",
      allowedValues: ["0", "1"],
      allowNA: false,
      naCondition: null,
    },
  ],
};
const messages = ["m1", "m2", "m3"].map((id, message_index) => ({
  id,
  message_index,
  timestamp: "00:01",
  speaker: "operator",
  text: "Здравствуйте",
}));
const evidence = {
  kind: "message",
  message_id: "m1",
  quote: "Здравствуйте",
  source_text: "Здравствуйте",
  verification_status: "verified",
  reason: null,
};
const criterionResult = {
  criterionId: "greeting",
  value: "1",
  explanation: "Есть приветствие",
  applicability_reason: null,
  evidence: [evidence],
  critical: false,
};
const score = {
  schema_version: "2.0",
  total: "100",
  earned: "0.125",
  possible: "0.125",
  applicable: 1,
  critical: false,
  results: [criterionResult],
};
const evaluation = {
  schema_version: "2.0",
  id: "job1",
  conversationId: "c1",
  conversationHash: "hash",
  checklist,
  results: [criterionResult],
  createdAt: "2026-11-07T10:00:00Z",
  source: "api",
  status: "completed",
  error: null,
  score,
};
const conversation = {
  schema_version: "2.0",
  id: "c1",
  contentHash: "hash",
  title: "Вопрос",
  operator: "Оператор",
  initials: "ОП",
  language: "RU",
  channel: "agent chat",
  date: null,
  duration: "00:01",
  transcript: messages,
  audioUrl: null,
  evaluation,
  scores: { original: score, final: score, sections: { Начало: score } },
};
const state = {
  schema_version: "2.0",
  data: { checklist, conversations: [conversation], appeals: [] },
  dataset: null,
  evaluationAvailable: true,
  revision: 1,
};

test("decimal fields round-trip without rounding, excessive precision stays invalid", () => {
  const view = models.checklistFromWire(checklist);
  assert.equal(view.criteria[0].type, "binary");
  assert.deepEqual(models.checklistToWire(view), checklist);
  for (const text of ["0.000001", "999999.999999", "1000000"])
    assert.equal(decimal.toDecimal(decimal.decimalInput(text)), text);
  for (const text of [
    "0.10000000000000001",
    "0.0000001",
    "1000000.000001",
    "1e-6",
  ])
    assert.ok(Number.isNaN(decimal.decimalInput(text)));
  assert.throws(() =>
    models.checklistToWire({
      ...view,
      criteria: [{ ...view.criteria[0], weight: 0.1234567 }],
    }),
  );
});

test("only verified message and range references become highlight IDs", () => {
  const input = {
    ...criterionResult,
    evidence: [
      {
        ...evidence,
        message_id: "m3",
        verification_status: "unverified",
        reason: "Цитата не найдена",
      },
      evidence,
      {
        ...evidence,
        kind: "absence",
        message_id: null,
        quote: null,
        start_message_id: "m1",
        end_message_id: "m2",
      },
      { ...evidence, message_id: "unknown" },
    ],
  };
  const result = models.resultFromWire(input, messages);
  assert.deepEqual(result.evidenceIds, ["m1", "m2"]);
  assert.equal(result.evidence[0].verified, false);
  assert.equal(result.evidence[0].reason, "Цитата не найдена");
});

test("incomplete or failed evaluations cannot expose a final score", () => {
  for (const status of [
    "queued",
    "running",
    "needs_review",
    "failed_validation",
    "failed",
    "retryable",
  ]) {
    const input = {
      ...state,
      data: {
        ...state.data,
        conversations: [
          {
            ...conversation,
            evaluation: { ...evaluation, status, score: null },
          },
        ],
      },
    };
    const view = models.stateFromWire(input).data.conversations[0];
    assert.equal(view.evaluation.status, status);
    assert.equal(view.scores, undefined);
  }
  assert.equal(
    models.stateFromWire(state).data.conversations[0].scores.final.total,
    100,
  );
});

test("outgoing mutation JSON uses generated v2 decimal and yes_no fields", async () => {
  const prior = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return Response.json(state);
  };
  try {
    await api.saveChecklistVersion(models.checklistFromWire(checklist), 1);
    await api.decideAppealRequest("appeal1", {
      status: "accepted",
      decision: "Уточнена оценка",
      newValue: 0.125,
    });
    await api.importWorkspace("[]");
    assert.equal(calls[0].body.schema_version, "2.0");
    assert.equal(calls[0].body.checklist.schema_version, "2.0");
    assert.equal(calls[0].body.checklist.criteria[0].type, "yes_no");
    assert.equal(calls[0].body.checklist.criteria[0].weight, "0.125");
    assert.equal(calls[1].body.newValue, "0.125");
    assert.equal(calls[2].body.text, "[]");
  } finally {
    globalThis.fetch = prior;
  }
});

test("organizer refresh sends the selected group and keeps dataset totals separate", async () => {
  const prior = globalThis.fetch;
  const calls = [];
  const dataset = {
    group: "CONTROL / Каз",
    total: 0,
    conversationIds: [],
    source: "live",
    fetchedAt: "2026-11-07T10:00:00Z",
  };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, method: init.method, body: JSON.parse(init.body) });
    return Response.json({ ...state, dataset });
  };
  try {
    const result = await api.refreshWorkspace(dataset.group);
    assert.deepEqual(calls[0], {
      url: "/api/organizer/refresh",
      method: "POST",
      body: { schema_version: "2.0", groupId: dataset.group },
    });
    assert.deepEqual(result.dataset, dataset);
    assert.equal(result.data.conversations.length, 1);
    await api.refreshWorkspace();
    assert.equal(calls[1].body.groupId, "DEV");
  } finally {
    globalThis.fetch = prior;
  }
});

test("structured API errors preserve retry and request metadata", async () => {
  const prior = globalThis.fetch;
  globalThis.fetch = async () =>
    Response.json(
      {
        schema_version: "2.0",
        code: "ADAPTER_UNAVAILABLE",
        message: "Модель недоступна",
        details: [],
        request_id: "request1",
        retryable: true,
      },
      { status: 503 },
    );
  try {
    await assert.rejects(
      api.startEvaluation("c1"),
      (error) =>
        error instanceof api.ApiRequestError &&
        error.message === "Модель недоступна" &&
        error.requestId === "request1" &&
        error.retryable,
    );
  } finally {
    globalThis.fetch = prior;
  }
});

test("evaluation endpoints accept direct jobs and version every POST", async () => {
  const prior = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return Response.json(
      {
        ...evaluation,
        status: calls.length === 1 ? "running" : "completed",
        score: calls.length === 1 ? null : score,
      },
      { status: calls.length === 1 ? 202 : 200 },
    );
  };
  try {
    assert.equal((await api.startEvaluation("c1")).status, "running");
    assert.equal((await api.fetchEvaluation("job1")).status, "completed");
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      schema_version: "2.0",
      conversationId: "c1",
    });
    assert.equal(calls[1].url, "/api/evaluations/job1");
  } finally {
    globalThis.fetch = prior;
  }
});

test("original critical filter retains findings removed by appeals and excludes unknown jobs", () => {
  const view = models.stateFromWire(state).data.conversations[0];
  const flagged = {
    ...view,
    scores: {
      ...view.scores,
      original: { ...view.scores.original, critical: true },
      final: { ...view.scores.final, critical: false },
    },
  };
  assert.equal(criticalStatus.originalCriticalStatus(flagged), "present");
  assert.equal(
    criticalStatus.originalCriticalStatus({
      ...view,
      scores: {
        ...view.scores,
        final: { ...view.scores.final, critical: true },
      },
    }),
    "absent",
  );
  for (const status of [
    "queued",
    "running",
    "needs_review",
    "failed_validation",
    "failed",
    "retryable",
  ])
    assert.equal(
      criticalStatus.originalCriticalStatus({
        ...flagged,
        evaluation: { ...view.evaluation, status },
      }),
      "unknown",
    );
  assert.equal(
    criticalStatus.originalCriticalStatus({ ...flagged, evaluation: null }),
    "unknown",
  );
  assert.equal(
    criticalStatus.originalCriticalStatus({ ...view, scores: undefined }),
    "unknown",
  );
});

test("saved versions use the list contract and evaluation sends the explicit selected version", async () => {
  const prior = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return Response.json(
      url === "/api/checklists"
        ? [{ ...checklist, version: 2 }, checklist]
        : { ...evaluation, status: "queued", score: null },
    );
  };
  try {
    const lists = await api.fetchChecklists();
    assert.deepEqual(
      lists.map((item) => item.version),
      [2, 1],
    );
    assert.equal(lists[1].criteria[0].weight, 0.125);
    await api.startEvaluation("c1", 1);
    assert.deepEqual(JSON.parse(requests[1].init.body), {
      schema_version: "2.0",
      conversationId: "c1",
      checklistVersion: 1,
    });
    globalThis.fetch = async () =>
      Response.json([{ ...checklist, schema_version: "1.0" }]);
    await assert.rejects(api.fetchChecklists(), /контракту v2.0/);
  } finally {
    globalThis.fetch = prior;
  }
});
