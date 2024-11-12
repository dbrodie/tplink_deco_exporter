FROM python:3.13-slim

WORKDIR /app

# Install Rust and required build dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Add cargo to PATH
ENV PATH="/root/.cargo/bin:${PATH}"

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
