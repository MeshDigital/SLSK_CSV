from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Track:
    """
    Represents a single track found on Soulseek or from an input source.
    """
    artist: Optional[str]
    title: Optional[str]
    album: Optional[str]
    filename: str
    size: Optional[int] # in bytes
    username: Optional[str]
    bitrate: Optional[int] # in kbps
    metadata: dict = field(default_factory=dict) # For additional, unstructured data

@dataclass
class Job:
    """
    Represents a download job, including its state and associated track.
    """
    id: Optional[int] # Unique identifier, typically from database
    track: Track
    status: str # e.g., PENDING, SEARCHING, DOWNLOADING, COMPLETED, FAILED, CANCELLED
    attempts: int
    created_at: datetime
    updated_at: datetime
    result_path: Optional[str] # Path to the downloaded file
    transfer_id: Optional[str] = None # aioslsk transfer ID for cancellation/resume
    partial_path: Optional[str] = None # Path to the temporary .part file
    bytes_downloaded: int = 0
    total_bytes: Optional[int] = None
    retry_at: Optional[datetime] = None # Timestamp for when to retry a failed job