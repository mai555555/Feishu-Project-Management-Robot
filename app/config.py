import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_id: str = os.getenv("FEISHU_APP_ID", "")
    app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    verification_token: str = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    encrypt_key: str = os.getenv("FEISHU_ENCRYPT_KEY", "")
    api_base_url: str = "https://open.feishu.cn/open-apis"
    ai_api_url: str = os.getenv(
        "AI_API_URL",
        "https://api.tourmaster.ch/v1beta/models/gemini-3.1-flash-lite:generateContent",
    )
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    data_dir: str = os.getenv("BOT_DATA_DIR", "data")


settings = Settings()
