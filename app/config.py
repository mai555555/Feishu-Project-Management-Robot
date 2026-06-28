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
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_api_url: str = os.getenv("TAVILY_API_URL", "https://api.tavily.com/search")
    data_dir: str = os.getenv("BOT_DATA_DIR", "data")
    feishu_root_department_id: str = os.getenv("FEISHU_ROOT_DEPARTMENT_ID", "0")
    bootstrap_admin_open_ids: str = os.getenv("BOT_BOOTSTRAP_ADMIN_OPEN_IDS", "")


settings = Settings()
