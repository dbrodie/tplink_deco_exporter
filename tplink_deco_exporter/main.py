from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from .api import TplinkDecoApi
from .config import load_config
from .log_forwarder import LogForwarder, LokiClient
from .probes import NodeProber
from .prometheus_metrics import DecoMetrics, DecoMetricsCollector

logger = logging.getLogger(__name__)


async def main():
    config = load_config()
    logging.basicConfig(
        level=config.logging.level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = {"started": time.time(), "metrics_success": False}
    async with aiohttp.ClientSession() as session:
        api = TplinkDecoApi(
            session,
            config.api.host.rstrip("/"),
            config.api.username,
            config.api.resolved_password(),
            config.api.verify_ssl,
            timeout_error_retries=config.api.timeout_retries,
            timeout_seconds=config.api.timeout_seconds,
        )
        metrics = DecoMetrics()
        collector = DecoMetricsCollector(api, metrics)

        async def metrics_loop():
            while True:
                try:
                    await collector.collect_metrics()
                    state["metrics_success"] = True
                except Exception as exc:  # noqa: BLE001 - keep later polls and HTTP alive
                    logger.error("Metrics collection failed: %s", type(exc).__name__)
                await asyncio.sleep(config.metrics.collection_interval)

        async def probe_loop():
            prober = NodeProber(config.probes, metrics)
            while True:
                started = time.monotonic()
                try:
                    if collector.devices:
                        await prober.probe(collector.devices)
                except Exception as exc:  # noqa: BLE001 - isolate probe cycles
                    metrics.collection_errors.labels("probes", type(exc).__name__).inc()
                else:
                    metrics.collection_last_success.labels("probes").set(time.time())
                finally:
                    metrics.collection_duration.labels("probes").set(
                        time.monotonic() - started
                    )
                await asyncio.sleep(config.probes.interval)

        async def logs_loop():
            forwarder = LogForwarder(
                api,
                LokiClient(session, config.loki, config.instance, metrics),
                config.logs,
                config.instance,
                metrics,
            )
            while True:
                started = time.monotonic()
                try:
                    timezone_name = str(
                        collector.time_settings.get("tz_region")
                        or config.logs.timezone_fallback
                    )
                    await forwarder.poll(timezone_name)
                except Exception as exc:  # noqa: BLE001 - preserve subsequent log polls
                    metrics.collection_errors.labels("logs", type(exc).__name__).inc()
                    logger.error("Log forwarding failed: %s", type(exc).__name__)
                else:
                    metrics.collection_last_success.labels("logs").set(time.time())
                finally:
                    metrics.collection_duration.labels("logs").set(
                        time.monotonic() - started
                    )
                await asyncio.sleep(config.logs.poll_interval)

        async def metrics_handler(_request):
            return web.Response(
                body=generate_latest(REGISTRY),
                headers={"Content-Type": CONTENT_TYPE_LATEST},
            )

        async def health_handler(_request):
            return web.json_response({"status": "ok"})

        async def ready_handler(_request):
            ready = bool(state["metrics_success"])
            return web.json_response(
                {"status": "ready" if ready else "starting"},
                status=200 if ready else 503,
            )

        app = web.Application()
        app.router.add_get("/metrics", metrics_handler)
        app.router.add_get("/healthz", health_handler)
        app.router.add_get("/readyz", ready_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, config.server.host, config.server.port).start()
        logger.info(
            "Exporter listening on %s:%s", config.server.host, config.server.port
        )
        tasks = [asyncio.create_task(metrics_loop())]
        if config.probes.enabled:
            tasks.append(asyncio.create_task(probe_loop()))
        if config.logs.enabled:
            tasks.append(asyncio.create_task(logs_loop()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
