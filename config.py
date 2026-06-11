# /config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Pydantic Settings Management for Fed Cascade.
    Automatically reads from the .env file in the root directory.
    """
    environment: str = "development"
    
    # Database - Pointed to the new Fed Cascade SQLite file
    database_url: str = "sqlite:///fed_cascade.db"
    
    # Application Config
    max_concurrent_api_requests: int = 5
    log_level: str = "WARNING"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

# Instantiate the global settings object
settings = Settings()