import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Server
    PORT = int(os.getenv("PORT", 8000))

    # Auth
    JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
    WORKER_API_KEY = os.getenv("WORKER_API_KEY", "change-me")

    # Dual Groq
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")
    GROQ_MODEL = os.getenv("GROQ_DEFAULT_MODEL", "openai/gpt-oss-120b")
    GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", 4096))
    GROQ_SAFE_TOKENS = int(os.getenv("GROQ_SAFE_TOKENS", 3500))

    # Agent
    AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", 5))
    AGENT_RETRY_WAIT = int(os.getenv("AGENT_RETRY_WAIT_SECONDS", 60))
    AGENT_LOOP_THRESHOLD = int(os.getenv("AGENT_LOOP_THRESHOLD", 3))
    AGENT_MAX_SUB_AGENTS = int(os.getenv("AGENT_MAX_SUB_AGENTS", 10))

    # RAG
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # Playwright
    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true") == "true"

    # bKash
    BKASH_NUMBER = os.getenv("BKASH_NUMBER", "01849691859")
    BKASH_AMOUNT = int(os.getenv("BKASH_AMOUNT", 1200))
    BKASH_ENTERPRISE_AMOUNT = int(os.getenv("BKASH_ENTERPRISE_AMOUNT", 5000))

    # Free tier
    FREE_AI_PROMPTS_DAILY = int(os.getenv("FREE_AI_PROMPTS_DAILY", 3))
    FREE_APK_BUILDS_DAILY = int(os.getenv("FREE_APK_BUILDS_DAILY", 1))

    # CORS
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    # Workspaces
    WORKSPACE_BASE = os.path.expanduser("~/yubilab-workspaces")

config = Config()
