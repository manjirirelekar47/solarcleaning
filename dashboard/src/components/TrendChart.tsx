import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { LossTrend } from "../types";

export type Range = "24h" | "7d";

interface Props {
  trend: LossTrend;
  range: Range;
}

const fmtTick = (ms: number, range: Range) =>
  new Date(ms).toLocaleString("en-IN", range === "24h" ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "short" });

export default function TrendChart({ trend, range }: Props) {
  if (trend.points.length === 0) {
    return <p className="note">No events in the last {range}. The chart fills in as the backend logs losses.</p>;
  }
  const data = trend.points.map((p) => ({ ts: new Date(p.t).getTime(), combined: p.combined, electrical: p.electrical, cnn: p.cnn }));
  return (
    <div className="chart" role="img" aria-label={`Loss trend over the last ${range}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--hair)" vertical={false} />
          <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(v) => fmtTick(v as number, range)} stroke="var(--muted)" fontSize={12} />
          <YAxis unit="%" stroke="var(--muted)" fontSize={12} />
          <Tooltip labelFormatter={(v) => new Date(v as number).toLocaleString("en-IN")} formatter={(v) => (typeof v === "number" ? `${v.toFixed(1)}%` : "n/a")} />
          <Legend />
          <Line type="monotone" dataKey="combined" name="Combined" stroke="var(--cell)" strokeWidth={2.5} dot={false} connectNulls />
          <Line type="monotone" dataKey="electrical" name="Electrical" stroke="var(--dust)" strokeWidth={1.5} dot={false} connectNulls />
          <Line type="monotone" dataKey="cnn" name="Camera" stroke="var(--ok)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls />
          {trend.cleanings.map((c) => (
            <ReferenceLine key={c.t} x={new Date(c.t).getTime()} stroke="var(--danger)" strokeDasharray="2 3" label={{ value: "clean", fill: "var(--danger)", fontSize: 11, position: "insideTop" }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
