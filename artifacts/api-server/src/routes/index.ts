import { Router, type IRouter } from "express";
import healthRouter from "./health";
import { dashboardAuth } from "./dashboard-auth";
import { createFlaskProxy, BOT1_ROUTES, BOT2_ROUTES } from "./flask-proxy";

// LIVE trading bot — mounted at /api (Flask on port 8000). Behavior unchanged.
const router: IRouter = Router();
router.use(healthRouter);
router.use(dashboardAuth);
router.use(createFlaskProxy({ port: 8000, routes: BOT1_ROUTES }));

// ANALYSIS-ONLY bot — mounted at /api2 (Flask on port 8001). Same dashboard
// password (dashboardAuth) and same open paths (/, /ping, /webhook); it proxies
// ONLY the June-21 snapshot's routes and never shares the live bot's port. The
// analysis bot itself (ANALYSIS_ONLY=1) cannot place orders or post to Discord
// and confines its DB access to the isolated `analysis_bot` schema.
const api2Router: IRouter = Router();
api2Router.use(dashboardAuth);
api2Router.use(createFlaskProxy({ port: 8001, routes: BOT2_ROUTES }));

export { api2Router };
export default router;
