import asyncio
import logging
from datetime import datetime
from pathlib import Path

from config import Config, load_config, save_config
from models import Track, Job
from aioslsk_adapter import AioSlskAdapter
from job_queue import JobQueue
from download_worker import DownloadWorker

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting example worker script...")

    # 1. Load configuration
    config_obj = load_config()
    config_obj.download_dir.mkdir(parents=True, exist_ok=True) # Ensure download dir exists
    logger.info(f"Configuration loaded: {config_obj}")

    # For this example, ensure username is set in config.ini or here
    if not config_obj.username:
        config_obj.username = "your_soulseek_username" # <<< IMPORTANT: SET YOUR USERNAME HERE
        save_config(config_obj)
        logger.warning("Username not set in config.ini. Please update it or set it in this script.")
        # return # Uncomment to stop if username is not set

    # 2. Create AioSlskAdapter in PyPI mode
    adapter = AioSlskAdapter(config_obj)

    # 3. Initialize JobQueue
    job_queue = JobQueue(config_obj)

    # 4. Define progress and status callbacks for the GUI (or console in this example)
    def progress_callback(job: Job, progress: float):
        logger.info(f"Job {job.id} ({job.track.title}): Progress {progress:.2%}")

    def status_callback(job: Job, status_text: str):
        logger.info(f"Job {job.id} ({job.track.title}): Status '{status_text}'")

    # 5. Instantiate DownloadWorker
    worker = DownloadWorker(adapter, job_queue, config_obj, progress_callback, status_callback)

    # 6. Enqueue a sample job (or load from CSV/Spotify)
    sample_track = Track(
        artist="Led Zeppelin",
        title="Stairway to Heaven",
        album="Led Zeppelin IV",
        filename="Stairway to Heaven.mp3", # Placeholder, will be updated by search
        size=None, username=None, bitrate=None
    )
    job_queue.enqueue(sample_track)

    # 7. Connect to Soulseek and run the worker
    await adapter.connect()
    await worker.run()

    # Let the worker run for a bit, or until all jobs are done
    # In a real GUI, the worker runs continuously in the background
    await asyncio.sleep(60) # Run for 60 seconds to process jobs

    await worker.stop()
    await adapter.disconnect()
    job_queue.close()
    logger.info("Example worker script finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script interrupted by user.")
    except Exception as e:
        logger.exception("An unhandled error occurred in the main script.")