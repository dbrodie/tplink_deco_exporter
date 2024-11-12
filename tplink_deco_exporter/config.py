import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field, ConfigDict

class ApiConfig(BaseModel):
    host: str = Field(..., env='DECO_API_HOST')
    username: str = Field(..., env='DECO_API_USERNAME')
    password: str = Field(..., env='DECO_API_PASSWORD')
    verify_ssl: bool = Field(default=True, env='DECO_API_VERIFY_SSL')
    timeout_error_retries: int = Field(default=3, env='DECO_API_TIMEOUT_RETRIES')
    timeout_seconds: int = Field(default=10, env='DECO_API_TIMEOUT_SECONDS')

class MetricsConfig(BaseModel):
    collection_interval: int = Field(default=60, env='DECO_METRICS_INTERVAL')

class ServerConfig(BaseModel):
    host: str = Field(default="0.0.0.0", env='DECO_SERVER_HOST')
    port: int = Field(default=9100, env='DECO_SERVER_PORT')

class LoggingConfig(BaseModel):
    level: str = Field(default="INFO", env='DECO_LOG_LEVEL')

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    api: ApiConfig
    metrics: MetricsConfig = MetricsConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load configuration from YAML file."""
        with open(path, 'r') as f:
            config_dict = yaml.safe_load(f)
        return cls.model_validate(config_dict)

    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls.model_validate({})

def load_config(config_path: str | Path | None = None) -> Config:
    """Load and validate configuration from file or environment variables."""
    # Check environment variable for config path
    env_config_path = os.getenv('CONFIG_PATH')
    
    # Determine the config path priority:
    # 1. Explicitly passed config_path
    # 2. CONFIG_PATH environment variable
    # 3. Default "config.yml" in current directory
    final_path = config_path or env_config_path or "config.yml"
    
    path = Path(final_path)
    if path.exists():
        return Config.from_yaml(path)
    
    # If no config file exists, try to load from environment variables
    return Config.from_env()
