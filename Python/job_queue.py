import sqlite3
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Optional, List

from models import Job, Track
from config import Config

logger = logging.getLogger(__name__)

class JobQueue:
    """
    Manages a queue of download jobs, persisting them to an SQLite database.
    Supports enqueueing, dequeuing, and retrieving job states.
    """
    def __init__(self, config: Config):
        self.db_path = config.db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Returns a database connection, creating it if necessary."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row # Access columns by name
        return self._conn

    def _init_db(self):
        """Initializes the database schema if it doesn't exist."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_path TEXT,
                transfer_id TEXT,
                partial_path TEXT,
                bytes_downloaded INTEGER DEFAULT 0,
                total_bytes INTEGER,
                retry_at TEXT
            )
        """)
        conn.commit()
        logger.info(f"Job queue database initialized at {self.db_path}")

    def _job_to_row(self, job: Job) -> tuple:
        """Converts a Job object to a tuple for database insertion/update."""
        return (
            json.dumps(asdict(job.track)),
            job.status,
            job.attempts,
            job.created_at.isoformat(),
            job.updated_at.isoformat(),
            job.result_path,
            job.transfer_id,
            job.partial_path,
            job.bytes_downloaded,
            job.total_bytes,
            job.retry_at.isoformat() if job.retry_at else None
        )

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        """Converts a database row to a Job object."""
        track_data = json.loads(row["track_json"])
        track = Track(**track_data)
        return Job(
            id=row["id"],
            track=track,
            status=row["status"],
            attempts=row["attempts"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            result_path=row["result_path"],
            transfer_id=row["transfer_id"],
            partial_path=row["partial_path"],
            bytes_downloaded=row["bytes_downloaded"],
            total_bytes=row["total_bytes"],
            retry_at=datetime.fromisoformat(row["retry_at"]) if row["retry_at"] else None
        )

    def enqueue(self, track: Track, status: str = "PENDING") -> int:
        """
        Adds a new track to the queue as a job.
        Returns the ID of the new job.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.now()
        job = Job(id=None, track=track, status=status, attempts=0, created_at=now, updated_at=now,
                  result_path=None, transfer_id=None, partial_path=None, bytes_downloaded=0, total_bytes=None, retry_at=None)
        cursor.execute(
            """INSERT INTO jobs (
                   track_json, status, attempts, created_at, updated_at, result_path,
                   transfer_id, partial_path, bytes_downloaded, total_bytes, retry_at
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._job_to_row(job)
        )
        conn.commit()
        job_id = cursor.lastrowid
        logger.info(f"Enqueued job {job_id}: {track.title}")
        return job_id # type: ignore

    def dequeue(self, status: str = "PENDING") -> Optional[Job]:
        """
        Retrieves the next job with the specified status from the queue that is ready for processing.
        A job is ready if its status matches and its retry_at timestamp is in the past or None.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute("SELECT * FROM jobs WHERE status = ? AND (retry_at IS NULL OR retry_at <= ?) ORDER BY created_at ASC LIMIT 1", (status, now_iso))
        row = cursor.fetchone()
        if row:
            job = self._row_to_job(row)
            logger.info(f"Dequeued job {job.id}: {job.track.title}")
            return job
        return None
    
    def update_job(self, job: Job):
        """Updates an existing job's fields in the database."""
        if job.id is None:
            raise ValueError("Cannot update job without an ID.")
        conn = self._get_conn()
        cursor = conn.cursor()
        job.updated_at = datetime.now() # Always update timestamp
        cursor.execute(
            """UPDATE jobs SET track_json = ?, status = ?, attempts = ?, updated_at = ?, result_path = ?, transfer_id = ?, partial_path = ?, bytes_downloaded = ?, total_bytes = ?, retry_at = ? WHERE id = ?""",
            (json.dumps(asdict(job.track)),
             job.status,
             job.attempts,
             job.updated_at.isoformat(),
             job.result_path,
             job.transfer_id,
             job.partial_path,
             job.bytes_downloaded,
             job.total_bytes,
             job.retry_at.isoformat() if job.retry_at else None,
             job.id)
        )
        conn.commit()
        logger.debug(f"Updated job {job.id} status to {job.status}")

    def find_by_transfer_id(self, transfer_id: str) -> Optional[Job]:
        """Retrieves a job by its transfer_id."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE transfer_id = ?", (transfer_id,))
        row = cursor.fetchone()
        if row:
            return self._row_to_job(row)
        return None

    def get_all_jobs(self) -> List[Job]:
        """Retrieves all jobs from the database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY created_at ASC")
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_jobs_by_status(self, status: str) -> List[Job]:
        """Retrieves jobs with a specific status."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC", (status,))
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def close(self):
        """Closes the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Job queue database connection closed.")