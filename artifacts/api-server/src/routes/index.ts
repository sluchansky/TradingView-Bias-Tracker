import { Router, type IRouter } from "express";
import healthRouter from "./health";
import flaskProxy from "./flask-proxy";

const router: IRouter = Router();

router.use(healthRouter);
router.use(flaskProxy);

export default router;
