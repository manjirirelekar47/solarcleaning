import { clock, pct } from "../format";
import type { CleaningMarker } from "../types";

export default function CleaningResult({ cleanings }: { cleanings: CleaningMarker[] }) {
  const last = cleanings[cleanings.length - 1];
  if (!last) return <p className="note">No cleaning cycle in this time range.</p>;
  const gained = last.pre !== null && last.post !== null ? last.pre - last.post : null;
  return (
    <div>
      <p className="ba">
        <span>{pct(last.pre)}</span>
        <span aria-hidden="true">→</span>
        <span>{pct(last.post)}</span>
      </p>
      <p className="sub">
        Loss before and after · {clock(last.t)} · result: <strong>{last.result ?? "in progress"}</strong>
      </p>
      {gained !== null && <p>{gained > 0 ? `Recovered ${gained.toFixed(1)} percentage points.` : "No measurable recovery."}</p>}
    </div>
  );
}
