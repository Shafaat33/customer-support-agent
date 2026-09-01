from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    openai_api_key: str
    llm_model: str = "gpt-4o-mini"
    app_env: str = "development"
    app_name: str = "customer-support-agent"


settings = Settings()
