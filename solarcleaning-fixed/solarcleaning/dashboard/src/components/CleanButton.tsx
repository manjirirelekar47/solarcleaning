import { useState } from "react";
import { ApiError, triggerClean } from "../api";

type Phase = "idle" | "confirm" | "sending";

export default function CleanButton({ busy, onDone }: { busy: boolean; onDone: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const send = async () => {
    setPhase("sending");
    try {
      setMessage({ ok: true, text: await triggerClean(apiKey) });
      onDone();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Something went wrong." });
    } finally {
      setPhase("idle");
    }
  };

  return (
    <div className="clean">
      <label>
        API key
        <input type="password" value={apiKey} autoComplete="off" onChange={(e) => setApiKey(e.target.value)} placeholder="X-API-Key" />
      </label>
      {phase === "confirm" ? (
        <div className="row">
          <button type="button" className="danger" onClick={send}>
            Confirm: spray the panel now
          </button>
          <button type="button" onClick={() => setPhase("idle")}>
            Cancel
          </button>
        </div>
      ) : (
        <button type="button" className="primary" disabled={!apiKey || busy || phase === "sending"} onClick={() => setPhase("confirm")}>
          {phase === "sending" ? "Starting…" : busy ? "Cleaning in progress" : "Clean now"}
        </button>
      )}
      {message && (
        <p className={`note ${message.ok ? "" : "note-error"}`} role="status">
          {message.text}
        </p>
      )}
    </div>
  );
}
