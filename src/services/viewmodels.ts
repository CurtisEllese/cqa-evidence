/**
 * Contract v2 boundary. Generated types describe JSON only; domain/types.ts
 * describes UI view models. The UI keeps `binary` and numeric controls for
 * compatibility, while the wire uses `yes_no` and exact decimal strings.
 * Decimal input is checked, never rounded, before serialization. Scoring is
 * exclusively server-owned. Only completed evaluations may expose scores.
 */
import type * as Wire from "../generated/contracts";
import type {
  AppData,
  Checklist,
  Criterion,
  CriterionResult,
  Evaluation,
  EvaluationScore,
  Conversation,
  Appeal,
  Message,
} from "../domain/types";
import { toDecimal } from "./decimal";

export type WorkspaceViewModel = {
  data: AppData;
  dataset: Wire.WorkspaceState["dataset"];
  datasets: Wire.DatasetInfo[];
  evaluationAvailable: boolean;
  revision: number;
  imported?: number;
};

function decimal(value: string): number {
  if (!/^-?\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value)))
    throw new Error("Сервер вернул недопустимую десятичную строку.");
  return Number(value);
}
export function checklistFromWire(checklist: Wire.Checklist): Checklist {
  return {
    ...checklist,
    criteria: checklist.criteria.map((criterion): Criterion => ({
      ...criterion,
      type: criterion.type === "yes_no" ? "binary" : criterion.type,
      weight: decimal(criterion.weight),
      max: decimal(criterion.max),
      allowedValues: criterion.allowedValues?.map(decimal),
      naCondition: criterion.naCondition ?? undefined,
    })),
  };
}
export function checklistToWire(checklist: Checklist): Wire.Checklist {
  return {
    ...checklist,
    schema_version: "2.0",
    criteria: checklist.criteria.map((criterion) => ({
      ...criterion,
      type: criterion.type === "binary" ? "yes_no" : criterion.type,
      weight: toDecimal(criterion.weight),
      max: toDecimal(criterion.max),
      allowedValues: criterion.allowedValues?.map(toDecimal) ?? [],
      naCondition: criterion.naCondition ?? null,
    })),
  };
}
export function resultFromWire(
  result: Wire.CriterionResult,
  messages: Message[] = [],
): CriterionResult {
  const evidenceIds = new Set<string>();
  for (const item of result.evidence) {
    if (item.verification_status !== "verified") continue;
    if (
      item.message_id &&
      messages.some((message) => message.id === item.message_id)
    )
      evidenceIds.add(item.message_id);
    const start = messages.findIndex(
      (message) => message.id === item.start_message_id,
    );
    const end = messages.findIndex(
      (message) => message.id === item.end_message_id,
    );
    if (start >= 0 && end >= start)
      for (const message of messages.slice(start, end + 1))
        evidenceIds.add(message.id);
  }
  return {
    criterionId: result.criterionId,
    value: result.value === null ? null : decimal(result.value),
    explanation: result.explanation,
    critical: result.critical,
    applicabilityReason: result.applicability_reason,
    assessmentStatus: result.assessment_status ?? "assessed",
    reviewReason: result.review_reason ?? null,
    evidenceIds: [...evidenceIds],
    evidence: result.evidence.map((item) => ({
      kind: item.kind ?? "message",
      messageId: item.message_id ?? null,
      quote: item.quote ?? null,
      sourceText: item.source_text,
      startMessageId: item.start_message_id ?? null,
      endMessageId: item.end_message_id ?? null,
      verified: item.verification_status === "verified",
      reason: item.reason,
    })),
  };
}
export function scoreFromWire(
  score: Wire.Score,
  messages: Message[] = [],
): EvaluationScore {
  return {
    total: score.total === null ? null : decimal(score.total),
    earned: decimal(score.earned),
    possible: decimal(score.possible),
    applicable: score.applicable,
    critical: score.critical,
    results: score.results.map((result) => resultFromWire(result, messages)),
    breakdown: score.breakdown ?? {},
  };
}
export function evaluationFromWire(
  evaluation: Wire.EvaluationResult,
  messages: Message[] = [],
): Evaluation {
  const status =
    evaluation.status === "completed" &&
    evaluation.results.some((result) => result.assessment_status === "unassessable")
      ? "needs_review"
      : evaluation.status;
  return {
    id: evaluation.id,
    checklist: checklistFromWire(evaluation.checklist),
    results: evaluation.results.map((result) =>
      resultFromWire(result, messages),
    ),
    createdAt: evaluation.createdAt,
    source: evaluation.source,
    status,
    error: evaluation.error?.message ?? null,
    errorCode: evaluation.error?.code ?? null,
    errorRetryable: evaluation.error?.retryable ?? null,
    total:
      status === "completed" && evaluation.score?.total != null
        ? decimal(evaluation.score.total)
        : null,
    provenance: evaluation.provenance ?? null,
  };
}
function conversationFromWire(
  conversation: Wire.ConversationDTO,
): Conversation {
  const evaluation = conversation.evaluation
    ? evaluationFromWire(conversation.evaluation, conversation.transcript)
    : null;
  return {
    ...conversation,
    evaluation,
    dateSource: conversation.dateSource ?? undefined,
    scores:
      evaluation?.status === "completed" && conversation.scores
        ? {
            original: scoreFromWire(
              conversation.scores.original,
              conversation.transcript,
            ),
            final: scoreFromWire(
              conversation.scores.final,
              conversation.transcript,
            ),
            sections: Object.fromEntries(
              Object.entries(conversation.scores.sections).map(
                ([section, score]) => [
                  section,
                  scoreFromWire(score, conversation.transcript),
                ],
              ),
            ),
          }
        : undefined,
  };
}
function appealFromWire(appeal: Wire.AppealDTO): Appeal {
  return {
    ...appeal,
    decision: appeal.decision ?? undefined,
    originalValue:
      appeal.originalValue === null ? null : decimal(appeal.originalValue),
    newValue:
      appeal.newValue === null || appeal.newValue === undefined
        ? appeal.newValue
        : decimal(appeal.newValue),
  };
}
export function stateFromWire(state: Wire.WorkspaceState): WorkspaceViewModel {
  return {
    dataset: state.dataset,
    datasets: state.datasets?.length
      ? state.datasets
      : state.dataset
        ? [state.dataset]
        : [],
    evaluationAvailable: state.evaluationAvailable,
    revision: state.revision,
    imported: state.imported ?? undefined,
    data: {
      checklist: checklistFromWire(state.data.checklist),
      conversations: state.data.conversations.map(conversationFromWire),
      appeals: state.data.appeals.map(appealFromWire),
    },
  };
}
