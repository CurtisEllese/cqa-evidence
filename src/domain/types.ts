export type Language = "RU" | "KZ" | "EN";
export type Channel = "agent chat" | "voice call";
export type Speaker = "operator" | "client" | "bot" | "system";
export type Message = {
  id: string;
  timestamp: string;
  speaker: Speaker;
  text: string;
};
export type Criterion = {
  id: string;
  title: string;
  description: string;
  weight: number;
  type: "binary" | "scale" | "flag";
  max: number;
  allowNA: boolean;
  allowedValues?: number[];
  naCondition?: string;
  section: string;
};
export type Checklist = {
  id: string;
  name: string;
  version: number;
  criteria: Criterion[];
};
export type CriterionResult = {
  criterionId: string;
  value: number | null;
  explanation: string;
  evidenceIds: string[];
  critical: boolean;
  applicabilityReason: string | null;
  evidence: Evidence[];
};
export type Evidence = {
  kind: "message" | "absence" | "not_applicable";
  messageId: string | null;
  quote: string | null;
  sourceText: string | null;
  startMessageId: string | null;
  endMessageId: string | null;
  verified: boolean;
  reason: string | null;
};
export type Evaluation = {
  id: string;
  checklist: Checklist;
  results: CriterionResult[];
  createdAt: string;
  source: "api";
  status:
    | "queued"
    | "running"
    | "completed"
    | "needs_review"
    | "failed_validation"
    | "failed"
    | "retryable";
  error: string | null;
  errorCode?: string | null;
  errorRetryable?: boolean | null;
  total?: number | null;
  provenance?: {
    provider: string;
    model: string;
    promptVersion: string;
  } | null;
};
export type Conversation = {
  id: string;
  title: string;
  operator: string;
  initials: string;
  language: Language;
  channel: Channel;
  date: string | null;
  dateSource?: "conversation" | "import";
  duration: string;
  transcript: Message[];
  audioUrl: string | null;
  audioPageUrl?: string | null;
  evaluation: Evaluation | null;
  scores?: {
    original: EvaluationScore;
    final: EvaluationScore;
    sections: Record<string, EvaluationScore>;
  };
};
export type EvaluationScore = {
  total: number | null;
  earned: number;
  possible: number;
  applicable: number;
  critical: boolean;
  results: CriterionResult[];
  breakdown?: Record<
    string,
    { weightedPoints: string; normalizedWeight: string; contribution: string }
  >;
};
export type Appeal = {
  id: string;
  conversationId: string;
  criterionId: string;
  evaluationId: string;
  comment: string;
  status: "pending" | "accepted" | "rejected";
  createdAt: string;
  decision?: string;
  newValue?: number | null;
  originalValue: number | null;
};
export type AppData = {
  conversations: Conversation[];
  checklist: Checklist;
  appeals: Appeal[];
};
