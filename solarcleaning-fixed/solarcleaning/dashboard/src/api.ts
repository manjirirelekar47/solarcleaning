import type { AlertRow, LossTrend, Readings, Status } from "./types";

export const API_URL: string = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    /* body was not JSON */
  }
  return `${res.status} ${res.statusText}`.trim();
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { signal });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, `Cannot reach the backend at ${API_URL}`);
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

export const getStatus = (s?: AbortSignal) => getJson<Status>("/status", s);
export const getLossTrend = (range: string, bucket: string, s?: AbortSignal) =>
  getJson<LossTrend>(`/loss-trend?range=${range}&bucket=${bucket}`, s);
export const getReadings = (s?: AbortSignal) =>
  getJson<Readings>("/readings?panel=both&bucket=1m&range=1h", s);
export const getAlerts = (s?: AbortSignal) => getJson<AlertRow[]>("/alerts?limit=30", s);
export const imageSrc = (url: string, id: number) => `${API_URL}${url}?v=${id}`;

export async function triggerClean(apiKey: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/trigger-clean`, { method: "POST", headers: { "X-API-Key": apiKey } });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_URL}`);
  }
  if (res.status === 401) throw new ApiError(401, "The API key was not accepted.");
  if (res.status === 409) throw new ApiError(409, "A cleaning cycle is already running.");
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return "Cleaning started.";
}
