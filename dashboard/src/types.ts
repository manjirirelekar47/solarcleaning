export type Level = "ok" | "watch" | "clean_recommended" | "critical";

export interface ImageInfo {
  id: number;
  timestamp: string | null;
  label: string | null;
  confidence: number | null;
  severity_score: number | null;
  severity_pct: number | null;
  model: string;
  context: string;
  used: boolean;
  note: string | null;
  url: string;
}

export interface Status {
  level: Level | string | null;
  action: string | null;
  combined_loss: number | null;
  electrical_loss: number | null;
  cnn_severity: number | null;
  reason: string | null;
  net_benefit_inr: number | null;
  event_time: string | null;
  data_age_s: number | null;
  device_health: "ok" | "degraded" | "offline" | "unknown";
  model: string;
  model_version: string | null;
  cleaning_active: boolean;
  latest_image: ImageInfo | null;
}

export interface TrendPoint {
  t: string;
  combined: number | null;
  electrical: number | null;
  cnn: number | null;
}

export interface CleaningMarker {
  t: string;
  pre: number | null;
  post: number | null;
  result: string | null;
}

export interface LossTrend {
  range: string;
  bucket_s: number;
  points: TrendPoint[];
  cleanings: CleaningMarker[];
}

export interface ReadingPoint {
  t: string;
  test?: number | null;
  reference?: number | null;
}

export interface Readings {
  range: string;
  bucket_s: number;
  points: ReadingPoint[];
}

export interface AlertRow {
  id: number;
  timestamp: string | null;
  kind: string;
  level: string | null;
  message: string;
  cycle_id: number | null;
}
