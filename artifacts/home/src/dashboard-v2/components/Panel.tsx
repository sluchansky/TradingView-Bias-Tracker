import type { CSSProperties, ReactNode } from "react";

export function DashboardPanel({
  title,
  eyebrow,
  children,
  className = "",
  style,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <section className={`dv2-panel ${className}`.trim()} style={style}>
      <header className="dv2-panel-heading">
        {eyebrow && <span className="dv2-eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
      </header>
      {children}
    </section>
  );
}

export function Unavailable({ children = "Unavailable" }: { children?: ReactNode }) {
  return <div className="dv2-unavailable">{children}</div>;
}

export function DataRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: ReactNode;
  tone?: "bull" | "bear" | "caution" | "info";
}) {
  return (
    <div className="dv2-data-row">
      <span>{label}</span>
      <strong className={tone ? `dv2-${tone}` : undefined}>{value}</strong>
    </div>
  );
}
