export function formatRiskPercentage(probability: number): string {
  const percentage = probability * 100;
  const fractionDigits = percentage < 1 ? 3 : percentage < 10 ? 2 : 1;
  return `${percentage.toFixed(fractionDigits)}%`;
}
