import threading
import time

from monitoring.monitor_config import (
    MONITOR_SITES
)

from monitoring.engine.monitor_engine import (
    MonitorEngine
)

from core.logger import setup_logger


logger = setup_logger(
    "run_monitor",
    "monitor"
)


threads = []


# ---------------------------------
# START MONITOR
# ---------------------------------
def start_monitor(config):

    site = config["site"]

    workers = config.get(
        "workers",
        20
    )

    interval = config.get(
        "interval",
        300
    )

    engine = MonitorEngine()

    thread = threading.Thread(

        target=engine.run,

        kwargs={

            "site": site,

            "workers": workers,

            "interval": interval
        },

        daemon=True
    )

    thread.start()

    threads.append(thread)

    logger.info(
        f"{site} | THREAD_STARTED"
    )

    print(
        f"[MONITOR STARTED] "
        f"{site} | "
        f"workers={workers} | "
        f"interval={interval}s"
    )


# ---------------------------------
# RUN ALL
# ---------------------------------
def run_all_monitors():

    print("\n=================================")
    print("   MONITORING SYSTEM STARTED")
    print("=================================\n")

    logger.info(
        "MONITOR_SYSTEM_STARTED"
    )

    for config in MONITOR_SITES:

        try:

            start_monitor(config)

        except Exception as e:

            logger.error(
                f"MONITOR_START_ERROR | {e}"
            )

    while True:

        time.sleep(1)