import { ageText } from "../format";
import type { Status } from "../types";

const OFFLINE_AFTER_S = 60;

interface Props {
  status: Status | null;
  backendError: string | null;
}

export default function StatusHeader({ status, backendError }: Props) {
  const stale = status?.data_age_s != null && status.data_age_s > OFFLINE_AFTER_S;
  const deviceDown = status?.device_health === "offline" || status?.device_health === "degraded";
  const level = status?.level ?? "unknown";

  return (
    <header className="masthead">
      <div>
        <h1>Solar soiling monitor</h1>
        <p className="sub">
          Data received {ageText(status?.data_age_s)}
          {status?.cleaning_active ? " · cleaning in progress" : ""}
        </p>
      </div>
      <div className="badges">
        <span className={`pill level-${level}`}>Level: {level}</span>
        {status && (
          <span className={`pill ${status.model === "cnn" ? "pill-neutral" : "pill-warn"}`}>
            {status.model === "cnn" ? `CNN ${status.model_version ?? ""}`.trim() : "Stub model: not used for decisions"}
          </span>
        )}
      </div>
      {backendError && (
        <p className="banner banner-error" role="alert">
          Backend unreachable. {backendError} Retrying every 5 s.
        </p>
      )}
      {!backendError && (stale || deviceDown) && (
        <p className="banner banner-warn" role="alert">
          {deviceDown
            ? `Device health: ${status?.device_health}. Automatic cleaning is blocked until the devices report again.`
            : `No new readings for ${ageText(status?.data_age_s).replace(" ago", "")}. Check the ESP32 and the network.`}
        </p>
      )}
    </header>
  );
}
