from prometheus_client import Counter, Gauge

from .api import TplinkDecoApi

# Prometheus metrics
DECO_DEVICE_INFO = Gauge('deco_device_info', 'Information about Deco devices', [
    'mac', 'model', 'hw_version', 'sw_version', 'name', 'ip_address', 'master',
    'connection_type', 'interface', 'bssid_band2_4', 'bssid_band5', 'signal_band2_4',
    'signal_band5'
])
DECO_DEVICE_ONLINE = Gauge('deco_device_online', 'Online status of Deco devices', ['mac'])
DECO_CLIENT_COUNT = Gauge('deco_client_count', 'Number of clients connected to Deco devices', ['mac'])
DECO_CLIENT_INFO = Gauge('deco_client_info', 'Information about clients connected to Deco devices', [
    'mac', 'name', 'ip', 'connection_type', 'interface', 'deco_mac'
])
DECO_CLIENT_UPLOAD = Gauge('deco_client_upload', 'Upload speed of clients in kilobytes per second', ['mac'])
DECO_CLIENT_DOWNLOAD = Gauge('deco_client_download', 'Download speed of clients in kilobytes per second', ['mac'])
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
                DECO_DEVICE_INFO.labels(
                    mac=device.mac,
                    model=device.device_model or '',
                    hw_version=device.hw_version or '',
                    sw_version=device.sw_version or '',
                    name=device.name or '',
                    ip_address=device.ip_address or '',
                    master=str(device.master or False).lower(),
                    connection_type=device.connection_type or '',
                    interface=device.interface or '',
                    bssid_band2_4=device.bssid_band2_4 or '',
                    bssid_band5=device.bssid_band5 or '',
                    signal_band2_4=str(device.signal_band2_4 or ''),
                    signal_band5=str(device.signal_band5 or '')
                ).set(1)
                DECO_DEVICE_ONLINE.labels(mac=device.mac).set(1 if device.internet_online else 0)

                try:
                    DECO_API_REQUESTS.labels(endpoint='list_clients').inc()
                    clients = await self.api.async_list_clients(device.mac)
                    DECO_CLIENT_COUNT.labels(mac=device.mac).set(len(clients))
                    for client in clients:
                        DECO_CLIENT_INFO.labels(
                            mac=client.mac,
                            name=client.name or '',
                            ip=client.ip_address or '',
                            connection_type=client.connection_type or '',
                            interface=client.interface or '',
                            deco_mac=client.deco_mac or ''
                        ).set(1)
                        
                        # Set upload and download speeds
                        DECO_CLIENT_UPLOAD.labels(mac=client.mac).set(client.up_kilobytes_per_s or 0)
                        DECO_CLIENT_DOWNLOAD.labels(mac=client.mac).set(client.down_kilobytes_per_s or 0)

                except Exception as e:
                    DECO_API_ERRORS.labels(type='list_clients').inc()
                    # Log the error, but continue processing other devices
                    print(f"Error collecting client metrics for device {device.mac}: {str(e)}")

        except Exception as e:
            DECO_API_ERRORS.labels(type='list_devices').inc()
            print(f"Error collecting device metrics: {str(e)}")
