// View models use JS numbers for form controls and charts only. Network values
// are canonical decimal strings. Never round user input at this boundary.
const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/;
export const isDecimalText = (value: string): boolean =>
  DECIMAL.test(value) && Number(value) <= 1_000_000;
export const decimalInput = (value: string): number =>
  isDecimalText(value) ? Number(value) : NaN;
export function toDecimal(value: number): string {
  const text = String(value);
  if (
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1_000_000 ||
    !DECIMAL.test(text)
  ) {
    throw new Error(
      "Число должно быть от 0 до 1 000 000 и содержать не более 6 знаков после запятой.",
    );
  }
  return text;
}
export function fromDecimal(value: string): number {
  if (!DECIMAL.test(value) || Number(value) > 1_000_000)
    throw new Error("Сервер вернул число вне контракта v2.");
  return Number(value);
}
export function isSupportedDecimal(value: number): boolean {
  try {
    toDecimal(value);
    return true;
  } catch {
    return false;
  }
}
