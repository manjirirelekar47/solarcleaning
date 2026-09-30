import type { ReactNode } from "react";

interface Props {
  title: string;
  loading?: boolean;
  error?: string | null;
  hasData?: boolean;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** Panel with a title and consistent loading / error states. Children render only with data. */
export default function Card({ title, loading, error, hasData = true, actions, className, children }: Props) {
  return (
    <section className={`card ${className ?? ""}`} aria-busy={loading && !hasData}>
      <header className="card-head">
        <h2>{title}</h2>
        {actions}
      </header>
      {error && (
        <p className="note note-error" role="alert">
          {hasData ? `Showing the last data received. ${error}` : error}
        </p>
      )}
      {hasData ? (
        <div className={error ? "stale" : undefined}>{children}</div>
      ) : (
        !error && <p className="note">{loading ? "Loading…" : "No data yet."}</p>
      )}
    </section>
  );
}
