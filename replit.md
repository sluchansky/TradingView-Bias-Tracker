# [Project name]

_Replace the heading above with the project's name, and this line with one sentence describing what this app does for users._

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

_Populate as you build — short repo map plus pointers to the source-of-truth file for DB schema, API contracts, theme files, etc._

- **Trading Academy / AI Trading Library** (learning-only knowledge module): backend in `artifacts/tradingview-webhook/app.py` (`/academy/*` routes + `_academy_*` helpers); dashboard `#view-academy` tab in the same file's `/dashboard` HTML; proxy whitelist in `artifacts/api-server/src/routes/flask-proxy.ts`. Safety smoke: `.local/state/academy_smoke.py` via `bash .local/state/check_academy.sh`.

## Architecture decisions

_Populate as you build — non-obvious choices a reader couldn't infer from the code (3-5 bullets)._

## Product

_Describe the high-level user-facing capabilities of this app once they exist._

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

- **Trading Academy is learning-only — never wire it into live trading.** The `/academy/*` module (sources → AI-extracted lessons → strategy cards → rules → validation → Q&A) is fully walled off from the gate/scoring/auto-execute/broker/sizing path, like `/backtest` and `/scalp-research`. Setting a strategy `APPROVED`/`active` records intent only; it does NOT place trades. Any change must keep the four strict goldens + parity byte-identical and pass `bash .local/state/check_academy.sh` (the money-path tripwire). Academy routes are owner-only: whitelisted in `flask-proxy.ts`, NEVER added to `dashboard-auth.ts` OPEN_PATHS.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
