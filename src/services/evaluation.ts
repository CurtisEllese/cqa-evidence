import type { Checklist, CriterionResult, EvaluationScore } from "../domain/types";
import type { ServerState } from "./api";

type WireCriterion = {
  id: string; title: string; description: string; type: "binary" | "scale" | "flag";
  weight: string; max: number; allowed_values: number[]; allow_na: boolean;
  na_condition: string | null; section: string;
};
type WireChecklist = { id: string; name: string; version: number; criteria: WireCriterion[] };
type WireResult = {
  criterion_id: string; value: number | null; explanation: string; na_reason: string | null;
  evidence: { kind: string; message_ids: string[]; start_message_id: string | null; end_message_id: string | null };
  evidence_status: "verified" | "unverified"; weight: string;
  effective_weight: string | null; contribution: string | null;
};
type WireSnapshot = {
  status: "completed" | "needs_review"; total_score: string | null; provisional_total_score: string | null;
  critical_error: boolean | null; results: WireResult[]; sections: { section: string; score: string | null }[];
};
type WireEvaluation = {
  id: string; created_at: string; checklist: WireChecklist; original: WireSnapshot; final: WireSnapshot;
};
export type EvaluationJob = {
  schema_version: "1.0"; id: string;
  status: "queued" | "running" | "completed" | "needs_review" | "failed";
  error: { error: { message: string } } | null;
};

function checklistForDisplay(list: WireChecklist): Checklist {
  return { id: list.id, name: list.name, version: list.version, criteria: list.criteria.map(c => ({
    id: c.id, title: c.title, description: c.description, type: c.type,
    weight: Number(c.weight), wireWeight: c.weight, max: c.max, allowedValues: c.allowed_values,
    allowNA: c.allow_na, naCondition: c.na_condition ?? undefined, section: c.section,
  })) };
}

function snapshotForDisplay(snapshot: WireSnapshot, checklist: Checklist, ids: string[]): EvaluationScore {
  const results: CriterionResult[] = snapshot.results.map(r => {
    const start = ids.indexOf(r.evidence.start_message_id ?? "");
    const end = ids.indexOf(r.evidence.end_message_id ?? "");
    const evidenceIds = (r.evidence.kind === "absence" || r.evidence.message_ids.length === 0) && start >= 0 && end >= start
      ? ids.slice(start, end + 1) : r.evidence.message_ids;
    return { criterionId: r.criterion_id, value: r.value,
      explanation: r.na_reason ? `${r.explanation} N/A: ${r.na_reason}` : r.explanation,
      evidenceIds, evidenceStatus: r.evidence_status, effectiveWeight: r.effective_weight,
      contribution: r.contribution,
      critical: checklist.criteria.some(c => c.id === r.criterion_id && c.type === "flag") && r.value === 1,
    };
  });
  return { total: snapshot.total_score === null ? null : Number(snapshot.total_score),
    applicable: snapshot.results.filter(r => r.value !== null && Number(r.weight) > 0).length,
    critical: snapshot.critical_error, results };
}

/** Decimal strings are converted only for presentation; totals come from the server. */
export function mapServerState(state: ServerState): ServerState {
  state.data.checklist.criteria = state.data.checklist.criteria.map(c => ({
    ...c, wireWeight: String(c.weight), weight: Number(c.weight),
  }));
  state.data.conversations = state.data.conversations.map(item => {
    const wire = (item as typeof item & { wire_evaluation?: WireEvaluation }).wire_evaluation;
    if (!wire) return item;
    const checklist = checklistForDisplay(wire.checklist);
    const ids = item.transcript.map(m => m.id);
    const original = snapshotForDisplay(wire.original, checklist, ids);
    const final = snapshotForDisplay(wire.final, checklist, ids);
    return { ...item,
      evaluationStatus: wire.final.status, provisionalScore: wire.final.provisional_total_score,
      evaluation: { id: wire.id, checklist, results: original.results, createdAt: wire.created_at, source: "api" as const, contractVersion: "1.0" as const },
      scores: { original, final, sections: Object.fromEntries(wire.final.sections.map(section => [section.section, {
        total: section.score === null ? null : Number(section.score), critical: wire.final.critical_error,
        applicable: wire.final.results.filter(r => r.value !== null && Number(r.weight) > 0 && checklist.criteria.some(c => c.id === r.criterion_id && c.section === section.section)).length,
        results: final.results.filter(r => checklist.criteria.some(c => c.id === r.criterionId && c.section === section.section)),
      }])) },
    };
  });
  return state;
}
