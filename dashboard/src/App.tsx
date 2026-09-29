import { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";

const API: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const COLOR: Record<string, string> = {
  ok: "#16a34a", watch: "#eab308", clean_recommended: "#f97316", critical: "#dc2626",
};

function usePoll<T>(path: string, ms = 3000): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    const load = () =>
      fetch(API + path).then((r) => r.json()).then(setData).catch(() => {});
    load();
    const id = setInterval(load, ms);
    return () => clearInterval(id);
  }, [path, ms]);
  return data;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function App() {
  const s = usePoll<Any>("/status");
  const trend = usePoll<Any[]>("/soiling-events?limit=200", 5000) ?? [];
  const alerts = usePoll<Any[]>("/alerts?limit=10", 5000) ?? [];
  const ev = s?.event;
  const color = COLOR[ev?.level ?? "ok"];
  const clean = async () => {
    const r = await fetch(API + "/trigger-clean", { method: "POST" });
    if (r.status === 409) alert("A cleaning cycle is already in progress");
  };

  return (
    <main style={{ display: "grid", gap: 16, padding: 16, gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))" }}>
      <section>{/* 1. Live Soiling Status */}
        <h3>Soiling status</h3>
        <div style={{ background: color, color: "#fff", padding: 16, borderRadius: 8 }}>
          {ev?.level ?? "waiting for data"}
        </div>
      </section>

      <section>{/* 2. Power Loss Gauge */}
        <h3>Power loss</h3>
        <div style={{ background: "#e5e7eb", borderRadius: 8 }}>
          <div style={{ width: `${Math.min(100, ev?.combined_loss ?? 0)}%`, minWidth: 48, background: color,
                        padding: 6, borderRadius: 8, color: "#fff", boxSizing: "border-box" }}>
            {(ev?.combined_loss ?? 0).toFixed(1)}%
          </div>
        </div>
        <small>
          Electrical {ev?.electrical_loss?.toFixed(1) ?? "n/a"}% · CNN {ev?.cnn_severity?.toFixed(0) ?? "n/a"}
        </small>
      </section>

      <section>{/* 3. Panel Image Feed */}
        <h3>Latest panel image</h3>
        {s?.image ? (
          <>
            <img src={API + s.image.url} style={{ maxWidth: "100%" }} />
            <div>{s.image.class} ({(s.image.confidence * 100).toFixed(0)}%)</div>
          </>
        ) : "No image yet"}
      </section>

      <section>{/* 4. Environmental Panel */}
        <h3>Environment</h3>
        <div>Temp {s?.env?.temp ?? "–"} °C · Humidity {s?.env?.humidity ?? "–"}%</div>
      </section>

      <section>{/* 5. Cleaning Status */}
        <h3>Cleaning</h3>
        {s?.cleaning ? (
          <div>
            #{s.cleaning.id}: {s.cleaning.status} {s.cleaning.result ?? ""}
            {s.cleaning.post != null && ` (${s.cleaning.pre.toFixed(1)}% → ${s.cleaning.post.toFixed(1)}%)`}
          </div>
        ) : "None yet"}
        <button onClick={clean}>Clean now</button>
      </section>

      <section style={{ gridColumn: "1 / -1" }}>{/* 6. Historical Trend */}
        <h3>Loss trend</h3>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={trend}>
            <XAxis dataKey="t" tickFormatter={(t: string) => new Date(t).toLocaleTimeString()} />
            <YAxis domain={[0, 40]} />
            <Tooltip />
            <ReferenceLine y={10} stroke="#f97316" strokeDasharray="4" />
            <ReferenceLine y={20} stroke="#dc2626" strokeDasharray="4" />
            <Line dataKey="loss" dot={false} stroke="#2563eb" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section style={{ gridColumn: "1 / -1" }}>{/* 7. Alert Log */}
        <h3>Alert log</h3>
        <ul>
          {alerts.map((a, i) => (
            <li key={i}>
              {new Date(a.t).toLocaleTimeString()} · {a.level} · {a.loss.toFixed(1)}% · {a.action}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
