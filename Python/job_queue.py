import sqlite3
import json
import logging
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
                result_path TEXT
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
            job.result_path
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
            result_path=row["result_path"]
        )

    def enqueue(self, track: Track, status: str = "PENDING") -> int:
        """
        Adds a new track to the queue as a job.
        Returns the ID of the new job.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.now()
        job = Job(id=None, track=track, status=status, attempts=0, created_at=now, updated_at=now, result_path=None)
        cursor.execute(
            """INSERT INTO jobs (track_json, status, attempts, created_at, updated_at, result_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            self._job_to_row(job)
        )
        conn.commit()
        job_id = cursor.lastrowid
        logger.info(f"Enqueued job {job_id}: {track.title}")
        return job_id # type: ignore

    def dequeue(self, status: str = "PENDING") -> Optional[Job]:
        """
        Retrieves and marks as 'DOWNLOADING' the next job with the specified status.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1", (status,))
        row = cursor.fetchone()
        if row:
            job = self._row_to_job(row)
            self.update_job_status(job.id, "DOWNLOADING")
            job.status = "DOWNLOADING" # Update in memory object too
            logger.info(f"Dequeued job {job.id}: {job.track.title}")
            return job
        return None

    def update_job_status(self, job_id: int, new_status: str, result_path: Optional[str] = None):
        """Updates the status and optionally the result path of a job."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.now()
        cursor.execute(
            """UPDATE jobs SET status = ?, updated_at = ?, result_path = ? WHERE id = ?""",
            (new_status, now.isoformat(), result_path, job_id)
        )
        conn.commit()
        logger.debug(f"Updated job {job_id} status to {new_status}")

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