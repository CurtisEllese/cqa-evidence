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
  wireWeight?: string;
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
  evidenceStatus?: "verified" | "unverified";
  effectiveWeight?: string | null;
  contribution?: string | null;
};
export type Evaluation = {
  id: string;
  checklist: Checklist;
  results: CriterionResult[];
  createdAt: string;
  source: "api";
  contractVersion?: "1.0";
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
  evaluationStatus?: "completed" | "needs_review";
  provisionalScore?: string | null;
  scores?: {
    original: EvaluationScore;
    final: EvaluationScore;
    sections: Record<string, EvaluationScore>;
  };
};
export type EvaluationScore = {
  total: number | null;
  earned?: number;
  possible?: number;
  applicable: number;
  critical: boolean | null;
  results: CriterionResult[];
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
