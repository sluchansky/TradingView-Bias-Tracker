import { Router, type IRouter } from "express";
import healthRouter from "./health";
import { dashboardAuth } from "./dashboard-auth";
import flaskProxy from "./flask-proxy";

const router: IRouter = Router();

router.use(healthRouter);
router.use(dashboardAuth);
router.use(flaskProxy);

export default router;
