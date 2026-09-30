import { useState } from "react";
import { getAlerts, getLossTrend, getReadings, getStatus } from "./api";
import AlertLog from "./components/AlertLog";
import Card from "./components/Card";
import CleanButton from "./components/CleanButton";
import CleaningResult from "./components/CleaningResult";
import CostBenefit from "./components/CostBenefit";
import ImagePanel from "./components/ImagePanel";
import LossGauge from "./components/LossGauge";
import PowerChart from "./components/PowerChart";
import StatusHeader from "./components/StatusHeader";
import TrendChart, { type Range } from "./components/TrendChart";
import { usePolling } from "./hooks/usePolling";

const BUCKET: Record<Range, string> = { "24h": "15m", "7d": "1h" };

export default function App() {
  const [range, setRange] = useState<Range>("24h");
  const status = usePolling(getStatus, 5_000);
  const trend = usePolling((s) => getLossTrend(range, BUCKET[range], s), 60_000, [range]);
  const readings = usePolling(getReadings, 30_000);
  const alerts = usePolling(getAlerts, 15_000);

  return (
    <main>
      <StatusHeader status={status.data} backendError={status.error} />
      <div className="grid">
        <Card title="Power loss" loading={status.loading} error={null} hasData={!!status.data} className="span-2">
          {status.data && <LossGauge status={status.data} />}
        </Card>
        <Card title="Latest image" loading={status.loading} error={null} hasData={!!status.data}>
          <ImagePanel image={status.data?.latest_image ?? null} />
        </Card>
        <Card
          title="Loss trend"
          loading={trend.loading}
          error={trend.error}
          hasData={!!trend.data}
          className="span-2"
          actions={
            <div className="toggle" role="group" aria-label="Time range">
              {(["24h", "7d"] as const).map((r) => (
                <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>
                  {r === "24h" ? "24 hours" : "7 days"}
                </button>
              ))}
            </div>
          }
        >
          {trend.data && <TrendChart trend={trend.data} range={range} />}
        </Card>
        <Card title="Last cleaning" loading={trend.loading} error={trend.error} hasData={!!trend.data}>
          {trend.data && <CleaningResult cleanings={trend.data.cleanings} />}
        </Card>
        <Card title="Test vs reference power" loading={readings.loading} error={readings.error} hasData={!!readings.data} className="span-2">
          {readings.data && <PowerChart readings={readings.data} />}
        </Card>
        <Card title="Cost and benefit" loading={status.loading} error={null} hasData={!!status.data}>
          {status.data && <CostBenefit status={status.data} />}
        </Card>
        <Card title="Alert log" loading={alerts.loading} error={alerts.error} hasData={!!alerts.data} className="span-2">
          {alerts.data && <AlertLog alerts={alerts.data} />}
        </Card>
        <Card title="Manual cleaning" hasData>
          <CleanButton busy={!!status.data?.cleaning_active} onDone={status.refresh} />
        </Card>
      </div>
    </main>
  );
}
