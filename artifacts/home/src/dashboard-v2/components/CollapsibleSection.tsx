import type { ReactNode } from "react";

export function CollapsibleSection({
  title,
  summary,
  children,
}: {
  title: string;
  summary: string;
  children: ReactNode;
}) {
  return (
    <details className="dv2-disclosure">
      <summary>
        <span>
          <strong>{title}</strong>
          <small>{summary}</small>
        </span>
        <i aria-hidden="true">+</i>
      </summary>
      <div className="dv2-disclosure-content">{children}</div>
    </details>
  );
}
