import asyncio
import logging
import os
import time
from typing import Callable, Optional
from pathlib import Path

from models import Job, Track
from config import Config
from aioslsk_adapter import AioSlskAdapter
from job_queue import JobQueue
import namer
import postprocessors
from datetime import datetime, timedelta

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
        self._last_progress_update_time: dict[int, float] = {} # {job_id: timestamp}
        self._progress_debounce_interval = 1 / 5 # 5 updates per second

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
            job = self.job_queue.dequeue("PENDING") # Dequeue only jobs ready for retry
            if job:
                await self._concurrency_semaphore.acquire()
                asyncio.create_task(self._process_job_wrapper(job))
            else:
                await asyncio.sleep(1) # Wait a bit if no jobs are available

    async def cancel_job(self, job_id: int):
        # TODO: Implement actual cancellation of running tasks.
        # This would require keeping track of the asyncio.Task for each job.
        logger.warning(f"Cancellation for job {job_id} requested but not fully implemented yet.")

    async def _process_job_wrapper(self, job: Job):
        """Wrapper to ensure semaphore is released after job processing and handle task cancellation."""
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
        job.attempts += 1 # Increment attempt count
        job.status = "SEARCHING"
        self.job_queue.update_job(job) # Persist updated attempts and status
        
        try:
            # 1. Search for the track
            # Construct a more robust search query
            search_query = f"{job.track.artist} {job.track.title}" if job.track.artist and job.track.title else job.track.filename
            search_results = await self.adapter.search(search_query)
            if not search_results:
                raise ValueError(f"No results found for {job.track.artist} - {job.track.title}")
            
            # For simplicity, pick the first result. In a real app, you'd have selection logic.
            best_result = search_results[0] 
            job.track.username = best_result.username
            job.track.filename = best_result.filename # Update filename from search result
            job.total_bytes = best_result.size # Update total_bytes from search result

            # 2. Format filename and create download directory
            formatted_filename = namer.format_filename(job.track, self.config.filename_template)
            final_output_path = self.config.download_dir / formatted_filename
            final_output_path.parent.mkdir(parents=True, exist_ok=True)

            # Determine temporary file path
            temp_file_path = final_output_path.with_suffix(final_output_path.suffix + ".part")
            job.partial_path = str(temp_file_path)

            # Check for existing partial file for resume
            if temp_file_path.exists():
                job.bytes_downloaded = temp_file_path.stat().st_size
                logger.info(f"Resuming download for job {job.id}. Already downloaded {job.bytes_downloaded} bytes.")
            else:
                job.bytes_downloaded = 0 # Reset if file doesn't exist or is new download

            self.job_queue.update_job(job) # Persist partial_path, bytes_downloaded, total_bytes

            # 3. Download the file
            job.status = "DOWNLOADING"
            self.job_queue.update_job(job)
            self.status_callback(job, "DOWNLOADING")

            transfer_id, progress_iterator = await self.adapter.download(
                best_result.username, best_result.filename, temp_file_path
            )

            if transfer_id:
                job.transfer_id = transfer_id
                self.job_queue.update_job(job) # Persist transfer_id

            async for progress_float in progress_iterator:
                # Update bytes_downloaded based on progress_float and total_bytes
                if job.total_bytes is not None:
                    job.bytes_downloaded = int(progress_float * job.total_bytes)
                
                # Debounce progress updates to the callback
                now = time.time()
                if job.id not in self._last_progress_update_time or \
                   (now - self._last_progress_update_time[job.id]) >= self._progress_debounce_interval:
                    self.progress_callback(job, progress_float)
                    self._last_progress_update_time[job.id] = now
                
                # Persist job state (including bytes_downloaded) periodically
                self.job_queue.update_job(job)

            # Ensure final progress update is sent
            if job.id in self._last_progress_update_time:
                del self._last_progress_update_time[job.id]
            self.progress_callback(job, 1.0) # Always send 100% on completion

            # 4. Atomically move file on completion
            if temp_file_path.exists():
                os.replace(temp_file_path, final_output_path)
                logger.info(f"Atomically moved {temp_file_path} to {final_output_path}")

            job.status = "COMPLETED"
            job.result_path = str(final_output_path)
            self.job_queue.update_job(job)
            self.status_callback(job, "COMPLETED")
            postprocessors.run_on_complete(job, self.config)
            logger.info(f"Job {job.id} completed: {job.track.title}")

        except asyncio.CancelledError:
            job.status = "CANCELLED"
            self.job_queue.update_job(job)
            self.status_callback(job, "CANCELLED")
            logger.info(f"Job {job.id} cancelled: {job.track.title}")
        except Exception as e:
            logger.error(f"Job {job.id} failed: {job.track.title} - {e}")
            if job.attempts < self.config.max_retries:
                # Exponential backoff: base * 2^(attempts-1)
                backoff_delay = 5 * (2 ** (job.attempts - 1)) # Start with 5s, then 10s, 20s...
                job.status = "PENDING" # Re-enqueue for retry
                job.retry_at = datetime.now() + timedelta(seconds=backoff_delay)
                self.job_queue.update_job(job)
                self.status_callback(job, f"FAILED (Retrying {job.attempts}/{self.config.max_retries} in {backoff_delay}s)")
            else:
                job.status = "FAILED"
                self.job_queue.update_job(job)
                self.status_callback(job, "FAILED")