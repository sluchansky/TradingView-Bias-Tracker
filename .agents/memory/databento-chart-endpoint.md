---
name: Databento live chart endpoint
description: GET /main-brain/chart — unified OHLCV + overlays endpoint; lightweight-charts v5 API changes; partial bar export pattern.
---

# Databento Live Chart Endpoint

## Rule
`GET /main-brain/chart?instrument=MGC&timeframe=1m&limit=300` is the canonical chart endpoint. Returns completed bars, partial bar, VWAP, structure events, and active trade overlay. Requires auth (operator's localStorage Basic Auth header forwarded by LiveMarketChart component).

**Why:** Display-only chart that reuses existing canonical stores without touching the gate/scoring/execution.

## How to apply
- Backend helpers: `_aggregate_bars_tf()` (1m→5m/15m), `_chart_connection_status()` — both in app.py near `get_databento_status()`.
- Partial bar export: `DATABENTO_PARTIAL_BY_INST` module-level dict in `databento_brain.py`. Updated inside `_tick_bar()` under `_partial_lock` with `dict(self._partial[inst])` copy. Flask routes import this directly.
- Proxy whitelist: `/main-brain/chart` is in `BOT1_ROUTES` in `flask-proxy.ts`.
- Structure events read from `ALERT_HISTORY` via `list()` snapshot (thread-safe, no lock needed).

## Instrument ID resolution (critical — do NOT rely on symbology_map)
`Live` client subscriptions with `stype_in="continuous"` never receive SymbolMappingMsg, so `client.symbology_map` stays permanently empty. `TradeMsg` also has no `.symbol` field. All three original resolution fallbacks (id map, sym lookup, prefix match) silently returned `inst=None` on every tick — no bars were ever built.

**Fix**: `_prefetch_id_map_http(db_module, api_key)` calls `db.Historical(key).symbology.resolve(dataset, symbols, stype_in="continuous", stype_out="instrument_id", start_date=today)` before the iterator starts. Populates `_id_to_inst` with current `{instrument_id: root}` pairs. `add_callback(_symmap_callback)` handles rollover mid-session. Both are fail-open.

**Why:** SDK v0.82.0 never sends SymbolMappingMsg on continuous-contract subscriptions; TradeMsg has no .symbol. HTTP pre-fetch is the only reliable resolution path.

## lightweight-charts v5 API (breaking changes from v4)
- `chart.addSeries(CandlestickSeries, opts)` not `chart.addCandlestickSeries(opts)`
- `chart.addSeries(LineSeries, opts)` not `chart.addLineSeries(opts)`
- `chart.addSeries(HistogramSeries, opts)` not `chart.addHistogramSeries(opts)`
- `createSeriesMarkers(series, markers)` (external function) — returns `ISeriesMarkersPluginApi` with `setMarkers()`; NOT `series.setMarkers()`
- Export names: `CandlestickSeries`, `LineSeries`, `HistogramSeries` (PascalCase aliases of lowercase module-level vars)

## Frontend component
`artifacts/home/src/components/LiveMarketChart.tsx` — self-contained, inserted in MainBrain.tsx after MarketStrip. Props: `ticker`, `onInstrumentChange`, `authHeader`. Polls every 5s with in-flight guard. Stops when collapsed. Cleanup on unmount via useEffect return.
