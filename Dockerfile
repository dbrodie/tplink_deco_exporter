FROM python:3.13-slim

WORKDIR /app

# Install build dependencies and curl for rustup
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Rust using rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
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
