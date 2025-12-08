import asyncio
import logging
from typing import Callable, Optional
from pathlib import Path

from models import Job, Track
from config import Config
from aioslsk_adapter import AioSlskAdapter
from job_queue import JobQueue
import namer
import postprocessors

logger = logging.getLogger(__name__)

class DownloadWorker:
    """
    Processes download jobs from the JobQueue with controlled concurrency.
    """
    def __init__(
        self,
        adapter: AioSlskAdapter,
        job_queue: JobQueue,
        config: Config,
        progress_callback: Callable[[Job, float], None],
        status_callback: Callable[[Job, str], None]
    ):
        self.adapter = adapter
        self.job_queue = job_queue
        self.config = config
        self.progress_callback = progress_callback
        self.status_callback = status_callback
        self._concurrency_semaphore = asyncio.Semaphore(self.config.concurrency)
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    async def run(self):
        """
        Starts the worker loop to process jobs from the queue.
        """
        if self._running:
            logger.warning("DownloadWorker is already running.")
            return
        self._running = True
        logger.info("DownloadWorker started.")
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        """
        Stops the worker loop gracefully.
        """
        if not self._running:
            logger.warning("DownloadWorker is not running.")
            return
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                logger.info("DownloadWorker loop cancelled.")
        logger.info("DownloadWorker stopped.")

    async def _worker_loop(self):
        """
        The main loop for the worker, continuously dequeuing and processing jobs.
        """
        while self._running:
            job = self.job_queue.dequeue("PENDING")
            if job:
                await self._concurrency_semaphore.acquire()
                asyncio.create_task(self._process_job_wrapper(job))
            else:
                await asyncio.sleep(1) # Wait a bit if no jobs are available

    async def _process_job_wrapper(self, job: Job):
        """Wrapper to ensure semaphore is released after job processing."""
        try:
            await self._process_job(job)
        finally:
            self._concurrency_semaphore.release()

    async def _process_job(self, job: Job):
        """
        Performs the search and download for a single job, updates its state,
        and handles retries.
        """
        self.status_callback(job, "SEARCHING")
        job.attempts += 1
        self.job_queue.update_job_status(job.id, "SEARCHING")
        
        try:
            # 1. Search for the track
            search_results = await self.adapter.search(f"{job.track.artist} - {job.track.title}")
            if not search_results:
                raise ValueError(f"No results found for {job.track.artist} - {job.track.title}")
            
            # For simplicity, pick the first result. In a real app, you'd have selection logic.
            best_result = search_results[0] 
            job.track.username = best_result.username
            job.track.filename = best_result.filename # Update filename from search result

            # 2. Format filename and create download directory
            formatted_filename = namer.format_filename(job.track, self.config.filename_template)
            output_path = self.config.download_dir / formatted_filename
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # 3. Download the file
            self.status_callback(job, "DOWNLOADING")
            async for progress in self.adapter.download(best_result.username, best_result.filename, output_path):
                self.progress_callback(job, progress)
            
            self.job_queue.update_job_status(job.id, "COMPLETED", str(output_path))
            self.status_callback(job, "COMPLETED")
            job.result_path = str(output_path)
            postprocessors.run_on_complete(job, self.config)
            logger.info(f"Job {job.id} completed: {job.track.title}")

        except asyncio.CancelledError:
            self.job_queue.update_job_status(job.id, "CANCELLED")
            self.status_callback(job, "CANCELLED")
            logger.info(f"Job {job.id} cancelled: {job.track.title}")
        except Exception as e:
            logger.error(f"Job {job.id} failed: {job.track.title} - {e}")
            if job.attempts < 3: # Simple retry policy
                self.job_queue.update_job_status(job.id, "PENDING") # Re-enqueue for retry
                self.status_callback(job, f"FAILED (Retrying {job.attempts}/3)")
                await asyncio.sleep(5) # Exponential backoff could be added here
            else:
                self.job_queue.update_job_status(job.id, "FAILED")
                self.status_callback(job, "FAILED")