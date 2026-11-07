import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(
  new URL("./conversationTitle.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { conversationTitle, callDateLabel, callTimeLabel } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const call = (
  title,
  date = "2026-11-03T15:49:13.1900670+00:00",
  extra = {},
) => ({
  id: "original-id",
  title,
  date,
  channel: "voice call",
  ...extra,
});

test("numeric and empty call titles become a dated call label", () => {
  for (const title of ["2", " 123 ", "", "—", "+123.45"])
    assert.equal(conversationTitle(call(title)), "Звонок от 3 ноября");
});
test("meaningful source title is preserved with its date", () => {
  for (const title of ["Алло.", "Вопрос по карте", "Қоңырау", "Call 2"])
    assert.equal(conversationTitle(call(title)), `${title} · 3 ноября`);
});
test("date is calculated in Asia/Qyzylorda across UTC midnight", () => {
  assert.equal(
    conversationTitle(call("2", "2026-11-03T20:00:00Z")),
    "Звонок от 4 ноября",
  );
  assert.equal(
    conversationTitle(call("2", "2026-11-03T18:59:59Z")),
    "Звонок от 3 ноября",
  );
});
test("missing, invalid or import-only date does not invent the call date", () => {
  for (const date of [null, "", "invalid"])
    assert.equal(
      conversationTitle(call("2", date)),
      "Звонок · дата не указана",
    );
  assert.equal(conversationTitle(call("Карта", null)), "Карта");
  assert.equal(
    conversationTitle(call("2", undefined, { dateSource: "import" })),
    "Звонок · дата не указана",
  );
});
test("source data, IDs and chat titles are untouched", () => {
  const original = Object.freeze(call("2"));
  conversationTitle(original);
  assert.equal(original.title, "2");
  assert.equal(original.id, "original-id");
  assert.equal(
    conversationTitle(call("2", undefined, { channel: "agent chat" })),
    "2",
  );
});
test("operator metadata and heading use the same date provenance and timezone", () => {
  const dated = call("2", "2026-11-03T20:00:00Z");
  assert.equal(callDateLabel(dated), "4 ноября");
  assert.equal(callDateLabel(dated, true), "4 ноября 2026 г.");
  for (const item of [
    call("2", null),
    call("2", "invalid"),
    call("2", undefined, { dateSource: "import" }),
  ]) {
    assert.equal(callDateLabel(item), null);
    assert.equal(callDateLabel(item, true), null);
  }
});
test("date-only has no invented time; timestamps use Qyzylorda and import time stays hidden", () => {
  assert.equal(callDateLabel(call("2", "2026-11-08")), "8 ноября");
  assert.equal(callTimeLabel(call("2", "2026-11-08")), null);
  assert.equal(callTimeLabel(call("2", "2026-11-03T20:00:00Z")), "01:00");
  assert.equal(callTimeLabel(call("2", "2026-11-04T01:00:00+05:00")), "01:00");
  assert.equal(callTimeLabel(call("2", null)), null);
  assert.equal(callTimeLabel(call("2", "invalid")), null);
  assert.equal(
    callTimeLabel(call("2", undefined, { dateSource: "import" })),
    null,
  );
});
