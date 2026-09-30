import { clock } from "../format";
import type { AlertRow } from "../types";

export default function AlertLog({ alerts }: { alerts: AlertRow[] }) {
  if (alerts.length === 0) return <p className="note">No alerts yet. A row appears only when something changes.</p>;
  return (
    <ul className="alerts">
      {alerts.map((a) => (
        <li key={a.id}>
          <span className={`pill level-${a.level ?? "unknown"}`}>{a.kind.replace("_", " ")}</span>
          <span>{a.message}</span>
          <time className="sub">{clock(a.timestamp)}</time>
        </li>
      ))}
    </ul>
  );
}
