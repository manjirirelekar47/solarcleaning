import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Readings } from "../types";

export default function PowerChart({ readings }: { readings: Readings }) {
  if (readings.points.length === 0) return <p className="note">No readings in the last hour.</p>;
  const data = readings.points.map((p) => ({ ts: new Date(p.t).getTime(), test: p.test ?? null, reference: p.reference ?? null }));
  return (
    <div className="chart" role="img" aria-label="Test and reference panel power, last hour">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--hair)" vertical={false} />
          <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(v) => new Date(v as number).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })} stroke="var(--muted)" fontSize={12} />
          <YAxis unit=" W" stroke="var(--muted)" fontSize={12} />
          <Tooltip labelFormatter={(v) => new Date(v as number).toLocaleTimeString("en-IN")} formatter={(v) => (typeof v === "number" ? `${v.toFixed(2)} W` : "n/a")} />
          <Legend />
          <Line type="monotone" dataKey="reference" name="Reference (clean)" stroke="var(--ok)" strokeWidth={2} dot={false} connectNulls />
          <Line type="monotone" dataKey="test" name="Test (soiled)" stroke="var(--dust)" strokeWidth={2} dot={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
