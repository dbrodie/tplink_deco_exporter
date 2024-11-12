FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY tplink_deco_exporter/ ./tplink_deco_exporter/

# Create config directory
RUN mkdir /config

# Set default config path
ENV CONFIG_PATH=/config/config.yml

# Expose prometheus metrics port
EXPOSE 9100

# Run the exporter
CMD ["python", "-m", "tplink_deco_exporter.main"]
