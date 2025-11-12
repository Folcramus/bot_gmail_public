import os
import json
from dotenv import load_dotenv
import logging

load_dotenv()


class Config:
    # Gmail configuration
    GMAIL_CLIENT_ID = os.getenv('GMAIL_CLIENT_ID')
    GMAIL_CLIENT_SECRET = os.getenv('GMAIL_CLIENT_SECRET')
    GMAIL_REFRESH_TOKEN = os.getenv('GMAIL_REFRESH_TOKEN')

    # Telegram configuration
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_GROUP_ID = int(os.getenv('TELEGRAM_GROUP_ID'))

    # Label to thread mapping
    LABEL_TO_THREAD_MAPPING = json.loads(os.getenv('LABEL_TO_THREAD_MAPPING'))

    # Other settings
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))  # 5 minutes by default
    MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', '4000'))


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/mail_bot.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)