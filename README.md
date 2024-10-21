# TP-Link Deco Prometheus Exporter

This project is a Prometheus exporter for TP-Link Deco routers. It provides metrics about the Deco system and connected devices, allowing you to monitor your home network using Prometheus and Grafana.

## Features

- Exports metrics for Deco routers and connected clients
- Local polling of the Deco admin web UI
- Prometheus-compatible metrics output

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/tplink-deco-exporter.git
   cd tplink-deco-exporter
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Configuration

Edit the `tplink_deco_exporter/main.py` file and update the following configuration variables:

```python
HOST = "http://192.168.0.1"  # The IP address of your main Deco router
USERNAME = "admin"  # Your Deco admin username
PASSWORD = "your_password"  # Your Deco admin password
VERIFY_SSL = True  # Set to False if your Deco uses a self-signed certificate
COLLECTION_INTERVAL = 60  # How often to collect metrics (in seconds)
```

## Usage

Run the exporter:

```
python -m tplink_deco_exporter.main
```

By default, the exporter will start a web server on port 9100. You can access the metrics at `http://localhost:9100/metrics`.

## Available Metrics

- `deco_device_info`: Information about Deco devices
- `deco_device_online`: Online status of Deco devices
- `deco_client_count`: Number of clients connected to Deco devices
- `deco_client_info`: Information about clients connected to Deco devices
- `deco_api_requests`: Number of API requests made to Deco devices
- `deco_api_errors`: Number of API errors encountered

## Prometheus Configuration

Add the following job to your Prometheus configuration:

```yaml
scrape_configs:
  - job_name: 'tplink_deco'
    static_configs:
      - targets: ['localhost:9100']
```

## Grafana Dashboard

You can create a Grafana dashboard to visualize the metrics collected by this exporter. Some suggested panels include:

- Deco devices status
- Number of connected clients per Deco
- Client connection types (2.4GHz, 5GHz, Ethernet)
- API request and error rates

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgements

This project was originally based on the Home Assistant integration for TP-Link Deco routers.
