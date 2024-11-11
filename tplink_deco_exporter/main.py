import asyncio
import logging

import aiohttp
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.core import REGISTRY

from .api import TplinkDecoApi
from .prometheus_metrics import DecoMetricsCollector
from .config import load_config

logger = logging.getLogger(__name__)

async def metrics_handler(request):
    logger.debug("Received request for /metrics endpoint")
    metrics = generate_latest(REGISTRY)
    logger.debug(f"Generated metrics response with size: {len(metrics)} bytes")
    return web.Response(
        body=metrics,
        content_type=CONTENT_TYPE_LATEST,
        charset='utf-8'
    )

async def collect_metrics(collector, interval):
    while True:
        logger.debug(f"Starting metrics collection cycle (interval: {interval}s)")
        try:
            await collector.collect_metrics()
            logger.debug("Successfully collected metrics")
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
        await asyncio.sleep(interval)

async def main():
    # Load configuration
    config = load_config()
    
    # Configure logging
    logging.basicConfig(
        level=config.logging.level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info("Starting TP-Link Deco Exporter")
    
    async with aiohttp.ClientSession() as session:
        # Initialize API with configuration
        api = TplinkDecoApi(
            session,
            config.api.host,
            config.api.username,
            config.api.password,
            config.api.verify_ssl,
            timeout_retries=config.api.timeout_error_retries,
            timeout_seconds=config.api.timeout_seconds
        )
        
        collector = DecoMetricsCollector(api)

        # Start the metrics collection loop
        asyncio.create_task(collect_metrics(
            collector,
            config.metrics.collection_interval
        ))

        # Set up the web server
        app = web.Application()
        app.router.add_get('/metrics', metrics_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.server.host, config.server.port)
        await site.start()

        logger.info(
            f"Prometheus exporter started on http://{config.server.host}:"
            f"{config.server.port}/metrics"
        )

        # Keep the server running
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
