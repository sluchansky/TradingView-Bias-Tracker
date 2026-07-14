import type { ReactNode } from "react";

export function CollapsibleSection({
  title,
  summary,
  badge,
  children,
}: {
  title: string;
  summary: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <details className="dv2-disclosure">
      <summary>
        <span>
          <strong>{title}</strong>
          <small>{summary}</small>
        </span>
        <span className="dv2-disclosure-actions">
          {badge && <b>{badge}</b>}
          <i aria-hidden="true">+</i>
        </span>
      </summary>
      <div className="dv2-disclosure-content">{children}</div>
    </details>
  );
}
