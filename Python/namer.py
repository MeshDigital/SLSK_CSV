import re
from pathlib import Path

from models import Track

def format_filename(track: Track, template: str) -> str:
    """
    Formats a filename based on a template and track metadata.
    Ensures the filename is safe for the filesystem.
    """
    # Extract extension from original filename if available, otherwise default to mp3
    ext = Path(track.filename).suffix.lstrip('.') if track.filename else "mp3"

    # Prepare data for templating, handling None values
    data = {
        "artist": track.artist or "Unknown Artist",
        "title": track.title or "Unknown Title",
        "album": track.album or "Unknown Album",
        "ext": ext
    }
    
    filename = template.format(**data)
    # Sanitize filename for filesystem
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename) # Replace invalid characters with underscore
    filename = re.sub(r'\s+', ' ', filename).strip() # Replace multiple spaces with single space
    return filename