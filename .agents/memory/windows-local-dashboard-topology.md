---
name: Windows local dashboard topology
description: Safety and process-ownership rules for a Windows-local live-chart dashboard.
---

The local dashboard launcher must start and own the Flask/Databento process and
the local Express bridge; it must not silently adopt an already-running service.
The bridge is proxy-only and must not load database routes or start migrations.

**Why:** The chart is an in-memory Databento cache, so a second or stale Flask
process makes the dashboard appear live while showing a different data stream.
Adopting an unmanaged process also cannot prove its execution and Discord
safety configuration. The normal API entry point has database lifecycle
responsibilities that are inappropriate for a read-only local chart bridge.

**How to apply:** Keep the local Vite `/api` bridge pointed at the launcher-owned
loopback proxy, have that proxy forward chart requests to the launcher-owned
Flask instance, and fail startup when either protected port is occupied. Apply
all execution and notification suppression flags after loading local
environment values, including deployment-derived Discord gates.