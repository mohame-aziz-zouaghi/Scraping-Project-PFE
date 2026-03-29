import requests
import random
import time
from core.headers import get_headers
from core.logger import setup_logger

session = requests.Session()
logger = setup_logger()

MAX_RETRIES = 3


def get(url):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            r = session.get(
                url,
                headers=get_headers(),
                timeout=20
            )

            if r.status_code == 200:

                #logger.info(f"HTTP | success | {url}")

                time.sleep(random.uniform(1.5, 5 ))

                return r.text

            else:

                logger.warning(
                    f"HTTP | bad status {r.status_code} | {url}"
                )

        except requests.RequestException as e:

            logger.error(
                f"HTTP | request error | {url} | {e}"
            )

        logger.warning(
            f"HTTP | retry {attempt}/{MAX_RETRIES} | {url}"
        )

        time.sleep(random.uniform(2, 4))

    logger.error(f"HTTP | failed after retries | {url}")

    return None