import { describe, expect, it } from "vitest";
import { initialData } from "./data";
import { parseImport } from "./import";
import { scoreEvaluation } from "./scoring";
import type { Appeal, Evaluation } from "./types";

const record = {
  conversation_id: "import-001",
  channel: "agent chat",
  language: "RU",
  audio_url: null,
  transcript: [
    { timestamp: "00:02", speaker: "operator", text: "Здравствуйте!" },
    { timestamp: "00:17", speaker: "client", text: "Как найти отчёт?" },
  ],
};
const voiceRecord = {
  ...record,
  conversation_id: "voice-001",
  channel: "voice call",
  transcript: [
    {
      timestamp: "2026-11-07T10:00:05+05:00",
      speaker: "operator",
      text: "Здравствуйте!",
    },
    {
      timestamp: "2026-11-07T05:02:22Z",
      speaker: "client",
      text: "Спасибо, понятно.",
    },
  ],
};
const testValues = [1, null, null, 15, 1, 7, 1, 0];
function evaluation(values: (number | null)[] = testValues): Evaluation {
  return {
    id: "test-evaluation-v1",
    checklist: structuredClone(initialData.checklist),
    createdAt: "2026-11-07T12:00:00+05:00",
    source: "api",
    results: initialData.checklist.criteria.map((criterion, index) => ({
      criterionId: criterion.id,
      value: values[index],
      explanation: "Synthetic scoring test only.",
      evidenceIds: [],
      critical: false,
    })),
  };
}
function appeal(patch: Partial<Appeal> = {}): Appeal {
  return {
    id: "test-appeal",
    conversationId: "test-conversation",
    evaluationId: "test-evaluation-v1",
    criterionId: "consultation",
    comment: "Прошу проверить.",
    status: "accepted",
    createdAt: "2026-11-07T12:00:00+05:00",
    originalValue: 15,
    newValue: 20,
    ...patch,
  };
}

describe("official rubric and deterministic scoring", () => {
  it("starts without fabricated conversations, evaluations or appeals", () => {
    expect(initialData.conversations).toEqual([]);
    expect(initialData.appeals).toEqual([]);
    expect(initialData.checklist.criteria).toHaveLength(8);
    expect(
      initialData.checklist.criteria.map((criterion) => criterion.weight),
    ).toEqual([10, 15, 20, 20, 15, 15, 5, 0]);
    expect(initialData.checklist.criteria[3].allowedValues).toEqual([
      0, 5, 10, 15, 20,
    ]);
    expect(initialData.checklist.criteria[5].allowedValues).toEqual([
      0, 4, 7, 10,
    ]);
    expect(initialData.checklist.criteria[7].type).toBe("flag");
    expect(
      initialData.checklist.criteria
        .filter((criterion) => criterion.allowNA)
        .every((criterion) => criterion.naCondition),
    ).toBe(true);
  });

  it("matches the official 85.38 example without rounding intermediate contributions", () => {
    expect(scoreEvaluation(evaluation())).toMatchObject({
      earned: 55.5,
      possible: 65,
      total: 85.38,
      applicable: 5,
      critical: false,
    });
  });

  it("adds a gross-error flag without altering the weighted score or applicability count", () => {
    const flagged = evaluation([1, null, null, 15, 1, 7, 1, 1]);
    const score = scoreEvaluation(flagged);
    expect(score).toMatchObject({
      earned: 55.5,
      possible: 65,
      total: 85.38,
      applicable: 5,
      critical: true,
    });
    expect(score.results.at(-1)?.critical).toBe(true);
    flagged.results[0].critical = true;
    flagged.results.at(-1)!.value = 0;
    expect(scoreEvaluation(flagged).critical).toBe(false);
    expect(scoreEvaluation(flagged).results[0].critical).toBe(false);
  });

  it("uses the saved checklist snapshot and never mutates original results", () => {
    const original = evaluation();
    const before = structuredClone(original);
    const changedSnapshot = structuredClone(original);
    changedSnapshot.checklist.criteria[0].weight = 20;
    expect(scoreEvaluation(changedSnapshot).total).toBe(87.33);
    expect(scoreEvaluation(original).total).toBe(85.38);
    expect(scoreEvaluation(original, [appeal()]).total).toBe(93.08);
    expect(original).toEqual(before);
  });

  it("applies only accepted appeals bound to the exact evaluation version", () => {
    for (const ignored of [
      appeal({ status: "pending" }),
      appeal({ status: "rejected" }),
      appeal({ evaluationId: "older-version" }),
    ]) {
      expect(scoreEvaluation(evaluation(), [ignored]).total).toBe(85.38);
    }
    expect(scoreEvaluation(evaluation(), [appeal()]).total).toBe(93.08);
  });

  it("treats accepted flag value 1 as a violation and 0 as its removal", () => {
    const flagAppeal = appeal({
      criterionId: "gross-errors",
      originalValue: 0,
      newValue: 1,
    });
    expect(scoreEvaluation(evaluation(), [flagAppeal])).toMatchObject({
      critical: true,
      total: 85.38,
      possible: 65,
    });
    const clearFlag = {
      ...flagAppeal,
      id: "second-decision",
      originalValue: 1,
      newValue: 0,
    };
    expect(
      scoreEvaluation(evaluation(), [flagAppeal, clearFlag]),
    ).toMatchObject({ critical: false, total: 85.38 });
    expect(
      scoreEvaluation(evaluation(), [
        { ...flagAppeal, evaluationId: "older-version" },
      ]).critical,
    ).toBe(false);
  });

  it("recalculates the denominator after an allowed N/A appeal", () => {
    const result = scoreEvaluation(evaluation(), [appeal({ newValue: null })]);
    expect(result).toMatchObject({
      earned: 40.5,
      possible: 45,
      total: 90,
      applicable: 4,
    });
  });

  it("returns no numeric score when no weighted criteria are applicable", () => {
    const onlyOptional = evaluation();
    onlyOptional.checklist.criteria = onlyOptional.checklist.criteria.filter(
      (criterion) => criterion.id === "discovery" || criterion.type === "flag",
    );
    onlyOptional.results = onlyOptional.results.filter(
      (result) =>
        result.criterionId === "discovery" ||
        result.criterionId === "gross-errors",
    );
    onlyOptional.results.find(
      (result) => result.criterionId === "gross-errors",
    )!.value = 1;
    expect(scoreEvaluation(onlyOptional)).toMatchObject({
      earned: 0,
      possible: 0,
      total: null,
      applicable: 0,
      critical: true,
    });
  });

  it("rejects values outside official discrete scales, invalid N/A and invalid appeals", () => {
    const invalid = evaluation();
    invalid.results[3].value = 12;
    expect(() => scoreEvaluation(invalid)).toThrow(/Недопустимое значение/);
    invalid.results[3].value = 15;
    invalid.results[5].value = 8;
    expect(() => scoreEvaluation(invalid)).toThrow(/Недопустимое значение/);
    invalid.results[5].value = 7;
    invalid.results[0].value = null;
    expect(() => scoreEvaluation(invalid)).toThrow(/N\/A/);
    expect(() =>
      scoreEvaluation(evaluation(), [appeal({ newValue: 18 })]),
    ).toThrow(/Недопустимое значение/);
    expect(() =>
      scoreEvaluation(evaluation(), [
        appeal({ criterionId: "gross-errors", newValue: 2 }),
      ]),
    ).toThrow(/Недопустимое значение/);
  });

  it("rejects incomplete or duplicated criterion coverage", () => {
    const missing = evaluation();
    missing.results.pop();
    expect(() => scoreEvaluation(missing)).toThrow(/ровно один ответ/);
    const duplicate = evaluation();
    duplicate.results[1].criterionId = duplicate.results[0].criterionId;
    expect(() => scoreEvaluation(duplicate)).toThrow(/ровно один ответ/);
  });
});

describe("atomic organizer import", () => {
  it("reads the organizer envelope, preserves timestamps, and generates stable message IDs", () => {
    const [conversation] = parseImport(
      JSON.stringify({
        error: "",
        isSuccess: true,
        data: { dataset_group_id: "DEV", total_records: 1, records: [record] },
      }),
      [],
    );
    expect(conversation).toMatchObject({
      id: "import-001",
      title: "Как найти отчёт?",
      language: "RU",
      channel: "agent chat",
      duration: "00:15",
      audioUrl: null,
      evaluation: null,
    });
    expect(conversation.transcript[0]).toEqual({
      ...record.transcript[0],
      id: "import-001-m1",
    });
  });

  it("deduplicates against existing IDs and other records in the same payload", () => {
    const second = { ...record, conversation_id: "import-002" };
    expect(
      parseImport(JSON.stringify([record, second, second]), ["import-001"]).map(
        (conversation) => conversation.id,
      ),
    ).toEqual(["import-002"]);
    expect(parseImport(JSON.stringify([record]), ["import-001"])).toEqual([]);
  });

  it.each([
    [{ ...record, channel: "email" }, /channel/],
    [{ ...record, language: "DE" }, /language/],
    [
      {
        ...record,
        transcript: [{ ...record.transcript[0], speaker: "unknown" }],
      },
      /speaker/,
    ],
    [
      { ...record, transcript: [{ ...record.transcript[0], text: " " }] },
      /текст/,
    ],
    [
      { ...record, transcript: [{ ...record.transcript[0], timestamp: "" }] },
      /timestamp/,
    ],
    [{ ...record, transcript: [] }, /transcript/],
    [{ ...record, audio_url: "javascript:alert(1)" }, /HTTP\(S\)/],
    [
      { ...record, audio_url: "https://user:password@example.com/audio.mp3" },
      /HTTP\(S\)/,
    ],
  ])(
    "rejects malformed records even when a previous record is valid",
    (invalid, error) => {
      expect(() => parseImport(JSON.stringify([record, invalid]), [])).toThrow(
        error,
      );
    },
  );

  it("rejects source errors, invalid JSON and size limits", () => {
    expect(() => parseImport("{", [])).toThrow(/JSON/);
    expect(() =>
      parseImport(
        JSON.stringify({ isSuccess: false, data: { records: [record] } }),
        [],
      ),
    ).toThrow(/isSuccess: false/);
    expect(() =>
      parseImport(
        JSON.stringify(Array.from({ length: 1001 }, () => record)),
        [],
      ),
    ).toThrow(/1000/);
    expect(() => parseImport(" ".repeat(5 * 1024 * 1024 + 1), [])).toThrow(
      /5 МБ/,
    );
  });

  it("accepts a voice record with an HTTPS audio URL", () => {
    expect(
      parseImport(
        JSON.stringify([
          { ...voiceRecord, audio_url: "https://example.com/audio.mp3" },
        ]),
        [],
      )[0].audioUrl,
    ).toBe("https://example.com/audio.mp3");
  });

  it("preserves voice ISO timestamps, derives its date, and computes duration across timezones", () => {
    const [conversation] = parseImport(JSON.stringify([voiceRecord]), []);
    expect(conversation.date).toBe("2026-11-07T10:00:05+05:00");
    expect(conversation.duration).toBe("02:17");
    expect(conversation.transcript.map((message) => message.timestamp)).toEqual(
      voiceRecord.transcript.map((message) => message.timestamp),
    );
    const reordered = {
      ...voiceRecord,
      transcript: [...voiceRecord.transcript].reverse(),
    };
    expect(parseImport(JSON.stringify([reordered]), [])[0].date).toBe(
      "2026-11-07T10:00:05+05:00",
    );
  });

  it.each([
    "invalid",
    "",
    "2026-02-30",
    "2026-11-07Tinvalid",
    "2026-11-07T10:00:00",
    null,
  ])("rejects an invalid explicit date: %s", (date) => {
    expect(() =>
      parseImport(JSON.stringify([{ ...record, date }]), []),
    ).toThrow(/date/);
  });

  it.each([
    "2026-11-07",
    "2026-02-30T10:00:00Z",
    "2026-11-07T24:00:00Z",
    "2026-11-07T10:00:60Z",
    "2026-11-07T10:00:00",
    "00:17",
    "Tinvalid",
  ])("rejects an invalid voice timestamp: %s", (timestamp) => {
    expect(() =>
      parseImport(
        JSON.stringify([
          {
            ...voiceRecord,
            transcript: [{ ...voiceRecord.transcript[0], timestamp }],
          },
        ]),
        [],
      ),
    ).toThrow(/timestamp/);
  });

  it.each([
    "00:60",
    "00:99",
    "00:1",
    "-01:01",
    "00:01:01",
    "2026-11-07T05:00:00Z",
    "Tinvalid",
  ])("rejects an invalid chat timestamp: %s", (timestamp) => {
    expect(() =>
      parseImport(
        JSON.stringify([
          { ...record, transcript: [{ ...record.transcript[0], timestamp }] },
        ]),
        [],
      ),
    ).toThrow(/timestamp/);
  });

  it("accepts long chat durations, explicit valid dates, and empty audio fields", () => {
    const [conversation] = parseImport(
      JSON.stringify([
        {
          ...record,
          date: "2026-11-07",
          audio_url: "  ",
          transcript: record.transcript.map((message, index) => ({
            ...message,
            timestamp: index ? "121:17" : "00:02",
          })),
        },
      ]),
      [],
    );
    expect(conversation).toMatchObject({
      duration: "121:15",
      date: "2026-11-07",
      audioUrl: null,
    });
    expect(
      parseImport(JSON.stringify([{ ...record, audio_url: "" }]), [])[0]
        .audioUrl,
    ).toBe(null);
    expect(
      parseImport(
        JSON.stringify([{ ...record, date: "2024-02-29T12:00:00Z" }]),
        [],
      )[0].date,
    ).toBe("2024-02-29T12:00:00Z");
  });
});
