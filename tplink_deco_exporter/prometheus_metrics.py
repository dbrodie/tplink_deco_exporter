from prometheus_client import Counter, Gauge

from .api import TplinkDecoApi

# Prometheus metrics
DECO_DEVICE_INFO = Gauge('deco_device_info', 'Information about Deco devices', ['mac', 'model', 'hw_version', 'sw_version'])
DECO_DEVICE_ONLINE = Gauge('deco_device_online', 'Online status of Deco devices', ['mac'])
DECO_CLIENT_COUNT = Gauge('deco_client_count', 'Number of clients connected to Deco devices', ['mac'])
DECO_CLIENT_INFO = Gauge('deco_client_info', 'Information about clients connected to Deco devices', ['mac', 'name', 'ip', 'connection_type'])
DECO_API_REQUESTS = Counter('deco_api_requests', 'Number of API requests made to Deco devices', ['endpoint'])
DECO_API_ERRORS = Counter('deco_api_errors', 'Number of API errors encountered', ['type'])

class DecoMetricsCollector:
    def __init__(self, api: TplinkDecoApi):
        self.api = api

    async def collect_metrics(self):
        try:
            DECO_API_REQUESTS.labels(endpoint='list_devices').inc()
            devices = await self.api.async_list_devices()
            for device in devices:
                mac = device['mac']
                DECO_DEVICE_INFO.labels(
                    mac=mac,
                    model=device.get('device_model', ''),
                    hw_version=device.get('hw_version', ''),
                    sw_version=device.get('sw_version', '')
                ).set(1)
                DECO_DEVICE_ONLINE.labels(mac=mac).set(1 if device.get('internet_online') else 0)

                try:
                    DECO_API_REQUESTS.labels(endpoint='list_clients').inc()
                    clients = await self.api.async_list_clients(mac)
                    DECO_CLIENT_COUNT.labels(mac=mac).set(len(clients))
                    for client in clients:
                        DECO_CLIENT_INFO.labels(
                            mac=client['mac'],
                            name=client['name'],
                            ip=client['ip'],
                            connection_type=client['connection_type']
                        ).set(1)
                except Exception as e:
                    DECO_API_ERRORS.labels(type='list_clients').inc()
                    # Log the error, but continue processing other devices
                    print(f"Error collecting client metrics for device {mac}: {str(e)}")

        except Exception as e:
            DECO_API_ERRORS.labels(type='list_devices').inc()
            print(f"Error collecting device metrics: {str(e)}")
