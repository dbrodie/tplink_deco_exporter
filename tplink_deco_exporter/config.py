import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field, ConfigDict

def to_env_var(field_name: str) -> str:
    """Convert field name to environment variable name."""
    return f"DECO_{field_name.upper()}"

class ApiConfig(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        env_prefix='DECO_API_',
        populate_by_name=True
    )
    
    host: str
    username: str = "admin"
    password: str
    verify_ssl: bool = True
    timeout_error_retries: int = 3
    timeout_seconds: int = 30

class MetricsConfig(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        env_prefix='DECO_METRICS_',
        populate_by_name=True
    )
    
    collection_interval: int = 60

class ServerConfig(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        env_prefix='DECO_SERVER_',
        populate_by_name=True
    )
    
    host: str = "0.0.0.0"
    port: int = 9100

class LoggingConfig(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        env_prefix='DECO_LOG_',
        populate_by_name=True
    )
    
    level: str = "INFO"

class Config(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        extra='forbid',
        populate_by_name=True
    )
    
    api: ApiConfig
    metrics: MetricsConfig = MetricsConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()

def load_config(config_path: str | Path | None = None) -> Config:
    """Load and validate configuration from file and/or environment variables."""
    # Check environment variable for config path
    env_config_path = os.getenv('CONFIG_PATH')
    
    # Determine the config path priority:
    # 1. Explicitly passed config_path
    # 2. CONFIG_PATH environment variable
    # 3. Default "config.yml" in current directory
    final_path = config_path or env_config_path or "config.yml"
    
    # Start with empty configuration structure
    config_dict = {
        'api': {},
        'metrics': {},
        'server': {},
        'logging': {}
    }
    
    # Load from file if it exists
    path = Path(final_path)
    if path.exists():
        with open(path, 'r') as f:
            file_config = yaml.safe_load(f)
            # Update our config dict with file values
            if file_config:
                for section in config_dict:
                    if section in file_config:
                        config_dict[section].update(file_config[section] or {})
    
    # Check for environment variables and override config values
    for env_var, value in os.environ.items():
        if env_var.startswith('DECO_'):
            parts = env_var.lower().split('_')
            if len(parts) >= 3:  # e.g., ['deco', 'api', 'password']
                section = parts[1]
                field = '_'.join(parts[2:])
                if section in config_dict:
                    config_dict[section][field] = value
    
    # Now validate the model
    return Config.model_validate(config_dict)
