import type { DashboardStatus, PricePoint } from "../types";
import { formatValue } from "../types";
import { DashboardPanel, Unavailable } from "./Panel";

type ChartLevel = {
  label: string;
  value: number;
  color: string;
};

export function ChartPanel({
  data,
  points,
}: {
  data: DashboardStatus | null;
  points: PricePoint[];
}) {
  const levels: ChartLevel[] = [
    { label: "Supply", value: Number(data?.nearest_supply), color: "#ef4444" },
    { label: "VWAP", value: Number(data?.vwap_value), color: "#3b82f6" },
    { label: "Demand", value: Number(data?.nearest_demand), color: "#22c55e" },
  ].filter((level) => Number.isFinite(level.value) && level.value > 0);

  const values = [...points.map((point) => point.price), ...levels.map((level) => level.value)];
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 0;
  const rawRange = maximum - minimum;
  const padding = rawRange > 0 ? rawRange * 0.12 : Math.max(maximum * 0.0005, 1);
  const low = minimum - padding;
  const high = maximum + padding;
  const range = high - low || 1;
  const width = 1_000;
  const height = 300;
  const plotTop = 18;
  const plotBottom = 270;
  const x = (index: number) => points.length <= 1 ? width / 2 : (index / (points.length - 1)) * width;
  const y = (price: number) => plotTop + ((high - price) / range) * (plotBottom - plotTop);
  const polyline = points.map((point, index) => `${x(index)},${y(point.price)}`).join(" ");
  const marketOpen = data?.market_open;
  const displayPrice = marketOpen === true
    ? data?.current_price
    : marketOpen === false
      ? data?.last_valid_price ?? data?.current_price
      : data?.current_price;

  return (
    <DashboardPanel
      title={marketOpen === true ? "Live price" : "Price chart"}
      eyebrow={marketOpen === true
        ? "Real samples from status polling"
        : marketOpen === false
          ? "Market closed · last valid snapshot"
          : "No live data available"}
      className="dv2-chart-panel"
    >
      <div className="dv2-chart-meta">
        <strong>{formatValue(displayPrice)}</strong>
        <span>
          {marketOpen === true
            ? `${points.length} live sample${points.length === 1 ? "" : "s"}`
            : marketOpen === false
              ? data?.last_valid_time ?? "No live sampling while closed"
              : "Sampling status unavailable"}
        </span>
      </div>
      {points.length ? (
        <div className="dv2-chart-wrap">
          <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Live price history">
            {[0, 1, 2, 3, 4].map((row) => {
              const gridY = plotTop + row * ((plotBottom - plotTop) / 4);
              return <line key={row} x1="0" x2={width} y1={gridY} y2={gridY} className="dv2-chart-grid" />;
            })}
            {levels.map((level) => (
              <g key={level.label}>
                <line
                  x1="0"
                  x2={width}
                  y1={y(level.value)}
                  y2={y(level.value)}
                  stroke={level.color}
                  strokeWidth="1.5"
                  strokeDasharray="8 7"
                  opacity="0.72"
                />
                <text x="10" y={y(level.value) - 6} fill={level.color} fontSize="12">
                  {level.label} {formatValue(level.value)}
                </text>
              </g>
            ))}
            {points.length > 1 && (
              <>
                <polyline points={polyline} className="dv2-price-glow" />
                <polyline points={polyline} className="dv2-price-line" />
              </>
            )}
            <circle
              cx={x(points.length - 1)}
              cy={y(points[points.length - 1].price)}
              r="5"
              className="dv2-price-dot"
            />
          </svg>
        </div>
      ) : (
        <Unavailable>
          {marketOpen === undefined
            ? "Waiting for live status data."
            : marketOpen === true
            ? "Waiting for the first real price sample."
            : "The market is closed. Live sampling will resume when it opens."}
        </Unavailable>
      )}
    </DashboardPanel>
  );
}
