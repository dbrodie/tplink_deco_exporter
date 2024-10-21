import asyncio

import aiohttp
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.core import REGISTRY

from .api import TplinkDecoApi
from .prometheus_metrics import DecoMetricsCollector

# Configuration (you might want to load these from a config file or environment variables)
HOST = "http://192.168.0.1"
USERNAME = "admin"
PASSWORD = "your_password"
VERIFY_SSL = True
COLLECTION_INTERVAL = 60  # seconds

async def metrics_handler(request):
    return web.Response(body=generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)

async def collect_metrics(collector):
    while True:
        await collector.collect_metrics()
        await asyncio.sleep(COLLECTION_INTERVAL)

async def main():
    async with aiohttp.ClientSession() as session:
        api = TplinkDecoApi(session, HOST, USERNAME, PASSWORD, VERIFY_SSL)
        collector = DecoMetricsCollector(api)

        # Start the metrics collection loop
        asyncio.create_task(collect_metrics(collector))

        # Set up the web server
        app = web.Application()
        app.router.add_get('/metrics', metrics_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 9100)
        await site.start()

        print(f"Prometheus exporter started on http://0.0.0.0:9100/metrics")

        # Keep the server running
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
