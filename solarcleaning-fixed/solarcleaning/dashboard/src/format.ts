export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "n/a" : `${value.toFixed(digits)}%`;
}

export function inr(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  const sign = value < 0 ? "-" : "";
  return `${sign}₹${Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function ageText(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "no data yet";
  if (seconds < 90) return `${Math.round(seconds)} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
