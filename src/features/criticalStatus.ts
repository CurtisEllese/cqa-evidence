import type { Conversation } from "../domain/types";

export type CriticalStatus = "present" | "absent" | "unknown";

// Appeals must not erase an original AI finding; incomplete jobs prove neither outcome.
export function originalCriticalStatus(
  conversation: Conversation,
): CriticalStatus {
  if (conversation.evaluation?.status !== "completed" || !conversation.scores)
    return "unknown";
  return conversation.scores.original.critical ? "present" : "absent";
}
