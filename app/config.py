from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env"}

    openai_api_key: str
    cerebras_api_key: str
    groq_api_key: str


settings = Settings()
