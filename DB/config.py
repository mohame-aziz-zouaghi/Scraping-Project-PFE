import os
from dotenv import load_dotenv

load_dotenv(encoding="utf-8")


class DBConfig:
    HOST = os.getenv("DB_HOST")
    PORT = int(os.getenv("DB_PORT", 5432))
    NAME = os.getenv("DB_NAME")
    USER = os.getenv("DB_USER")
    PASSWORD = os.getenv("DB_PASSWORD")