import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

/** Fetch now, then every intervalMs. Keeps the last good data when a refresh fails. */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  deps: ReadonlyArray<unknown> = [],
): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      try {
        const result = await fetcherRef.current(controller.signal);
        setData(result);
        setError(null);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setError((err as Error).message);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = window.setTimeout(run, intervalMs);
        }
      }
    };
    setLoading(true);
    void run();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, refresh };
}
