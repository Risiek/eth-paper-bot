"""Single entrypoint for Fly.io: bot loop + Flask dashboard in one process."""
import threading
import time
import logging

from config import CYCLE_SECONDS, LOG_FILE

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("run")


def bot_loop():
    from bot import run_once
    log.info("bot thread started")
    while True:
        try:
            run_once()
        except Exception as e:
            log.exception(f"bot cycle error: {e}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    log.info("starting flask")

    from app import app
    app.run(host="0.0.0.0", port=5000, debug=False)
