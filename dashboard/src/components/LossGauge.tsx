import { pct } from "../format";
import type { Status } from "../types";

const COLS = 10;
const ROWS = 6;
const FULL_HAZE_AT = 25; // % loss at which the dust film is at full strength

interface Props {
  status: Status;
}

/** A panel drawn as cells; the amber film over it thickens with the measured loss. */
export default function LossGauge({ status }: Props) {
  const loss = status.combined_loss;
  const haze = loss === null ? 0 : Math.min(Math.max(loss / FULL_HAZE_AT, 0), 1);
  const cellW = 30;
  const cellH = 24;
  const gap = 3;
  const width = COLS * (cellW + gap) + gap;
  const height = ROWS * (cellH + gap) + gap;

  return (
    <div className="gauge">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={loss === null ? "No loss figure available" : `Estimated power loss ${pct(loss)}`}
      >
        <rect width={width} height={height} rx="3" fill="var(--frame)" />
        {Array.from({ length: ROWS * COLS }, (_, i) => {
          const x = gap + (i % COLS) * (cellW + gap);
          const y = gap + Math.floor(i / COLS) * (cellH + gap);
          return <rect key={i} x={x} y={y} width={cellW} height={cellH} rx="1.5" fill="var(--cell)" />;
        })}
        <rect width={width} height={height} rx="3" fill="var(--dust)" opacity={haze * 0.72} />
      </svg>
      <div className="gauge-read">
        <p className="big">{loss === null ? "No figure" : pct(loss)}</p>
        <p className="sub">{loss === null ? "Not enough light or no valid reading" : "estimated power loss"}</p>
        <dl className="pairs">
          <dt>Electrical</dt>
          <dd>{pct(status.electrical_loss)}</dd>
          <dt>Camera (CNN)</dt>
          <dd>{pct(status.cnn_severity)}</dd>
          <dt>Decision</dt>
          <dd>{status.action ?? "n/a"}</dd>
        </dl>
      </div>
    </div>
  );
}
