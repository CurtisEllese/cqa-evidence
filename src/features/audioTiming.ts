// ISO timestamps are wall-clock times. Their distance from the first utterance
// is only an estimate of audio position; the operator can correct the offset.
export function audioPosition(
  first: string,
  timestamp: string,
  offset: number,
): number | null {
  if (!Number.isFinite(offset)) return null;
  const seconds = (value: string): number | null => {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) {
      const milliseconds = Date.parse(value);
      return Number.isFinite(milliseconds) ? milliseconds / 1000 : null;
    }
    const match = /^(\d+):([0-5]\d)(?::([0-5]\d))?$/.exec(value);
    if (!match) return null;
    return match[3] === undefined
      ? Number(match[1]) * 60 + Number(match[2])
      : Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
  };
  if (/T/.test(first) !== /T/.test(timestamp)) return null;
  const start = seconds(first),
    target = seconds(timestamp);
  if (start === null || target === null || target < start) return null;
  return Math.max(0, target - start + offset);
}
