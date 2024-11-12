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

## Docker

You can run this exporter using Docker in two ways:

### Using Docker Compose

1. With a config file:
   ```yaml
   version: '3'
   services:
     tplink-deco-exporter:
       build: .
       ports:
         - "9100:9100"
       volumes:
         - ./config.yml:/config/config.yml:ro
       restart: unless-stopped
   ```

2. With environment variables:
   ```yaml
   version: '3'
   services:
     tplink-deco-exporter:
       build: .
       ports:
         - "9100:9100"
       environment:
         - DECO_API_HOST=http://192.168.0.1
         - DECO_API_USERNAME=admin
         - DECO_API_PASSWORD=your_password
         - DECO_API_VERIFY_SSL=true
         - DECO_API_TIMEOUT_RETRIES=3
         - DECO_API_TIMEOUT_SECONDS=10
         - DECO_METRICS_INTERVAL=60
         - DECO_SERVER_HOST=0.0.0.0
         - DECO_SERVER_PORT=9100
         - DECO_LOG_LEVEL=INFO
       restart: unless-stopped
   ```

Run with:
```bash
docker-compose up -d
```

### Using Docker Directly

1. Build the image:
   ```bash
   docker build -t tplink-deco-exporter .
   ```

2. Run with a config file:
   ```bash
   docker run -d \
     -p 9100:9100 \
     -v $(pwd)/config.yml:/config/config.yml:ro \
     --name tplink-deco-exporter \
     tplink-deco-exporter
   ```

3. Or run with environment variables:
   ```bash
   docker run -d \
     -p 9100:9100 \
     -e DECO_API_HOST=http://192.168.0.1 \
     -e DECO_API_USERNAME=admin \
     -e DECO_API_PASSWORD=your_password \
     -e DECO_API_VERIFY_SSL=true \
     -e DECO_METRICS_INTERVAL=60 \
     --name tplink-deco-exporter \
     tplink-deco-exporter
   ```

## Configuration

The exporter can be configured using either a YAML file or environment variables.

### Configuration File (config.yml)

```yaml
api:
  host: "http://192.168.0.1"
  username: "admin"
  password: "your_password"
  verify_ssl: true
  timeout_error_retries: 3
  timeout_seconds: 10

metrics:
  collection_interval: 60  # seconds

server:
  host: "0.0.0.0"
  port: 9100

logging:
  level: "INFO"
```

### Environment Variables

All configuration options can be set using environment variables:

- `DECO_API_HOST`: The IP address of your main Deco router
- `DECO_API_USERNAME`: Your Deco admin username
- `DECO_API_PASSWORD`: Your Deco admin password
- `DECO_API_VERIFY_SSL`: Set to "true" or "false"
- `DECO_API_TIMEOUT_RETRIES`: Number of retries on timeout
- `DECO_API_TIMEOUT_SECONDS`: Timeout in seconds
- `DECO_METRICS_INTERVAL`: How often to collect metrics (in seconds)
- `DECO_SERVER_HOST`: Server host (default: "0.0.0.0")
- `DECO_SERVER_PORT`: Server port (default: 9100)
- `DECO_LOG_LEVEL`: Logging level (default: "INFO")

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
