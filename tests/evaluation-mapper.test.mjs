import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/services/evaluation.ts", import.meta.url), "utf8");
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { mapServerState } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`);
const fixture = () => JSON.parse(readFileSync(new URL("../backend/tests/fixtures/state-response.json", import.meta.url), "utf8"));

test("mapper displays the server total, without computing scores from model values", () => {
  const state = fixture();
  state.data.conversations[0].wire_evaluation.final.results[0].value = 0;
  const mapped = mapServerState(state).data.conversations[0];
  assert.equal(mapped.scores.final.total, 85.38);
  assert.equal(mapped.evaluation.contractVersion, "1.0");
  assert.equal(mapped.evaluation.checklist.criteria[0].wireWeight, "10");
});

test("unverified evidence cannot display the provisional score as confirmed", () => {
  const state = fixture();
  const snapshot = state.data.conversations[0].wire_evaluation.final;
  Object.assign(snapshot, { status: "needs_review", total_score: null, provisional_total_score: "85.38", critical_error: null });
  snapshot.results[0].evidence_status = "unverified";
  const mapped = mapServerState(state).data.conversations[0];
  assert.equal(mapped.scores.final.total, null);
  assert.equal(mapped.scores.final.critical, null);
  assert.equal(mapped.provisionalScore, "85.38");
  assert.equal(mapped.evaluationStatus, "needs_review");
  assert.equal(mapped.scores.final.results[0].evidenceStatus, "unverified");
});

test("N/A checked ranges remain navigable, including system and bot messages", () => {
  const state = fixture();
  const evidence = state.data.conversations[0].wire_evaluation.final.results[1].evidence;
  Object.assign(evidence, { message_ids: [], start_message_id: "synthetic-kz-m1", end_message_id: "synthetic-kz-m3" });
  const mapped = mapServerState(state).data.conversations[0];
  assert.deepEqual(mapped.scores.final.results[1].evidenceIds, ["synthetic-kz-m1", "synthetic-kz-m2", "synthetic-kz-m3"]);
});
