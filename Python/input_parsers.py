import csv
import logging
from typing import List

from models import Track

logger = logging.getLogger(__name__)

def parse_csv(path: str) -> List[Track]:
    """
    Parses a CSV file into a list of Track objects.
    Expected columns: artist, title, album (optional).
    """
    tracks: List[Track] = []
    try:
        with open(path, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                artist = row.get("artist")
                title = row.get("title")
                album = row.get("album")
                if artist and title:
                    # Filename is a placeholder, will be formatted later
                    tracks.append(Track(artist=artist, title=title, album=album, filename="", size=None, username=None))
                else:
                    logger.warning(f"Skipping CSV row due to missing artist or title: {row}")
    except FileNotFoundError:
        logger.error(f"CSV file not found: {path}")
    except Exception as e:
        logger.exception(f"Error parsing CSV file {path}: {e}")
    return tracks

async def expand_spotify_url(url: str) -> List[Track]:
    """
    Placeholder: Expands a Spotify URL (playlist, album, track) into a list of Track objects.
    Requires integration with Spotify API.
    """
    logger.warning(f"Spotify URL expansion not implemented. URL: {url}")
    return []

async def expand_youtube_url(url: str) -> List[Track]:
    """
    Placeholder: Expands a YouTube URL into a list of Track objects.
    Requires integration with YouTube API or a library like youtube-dl/yt-dlp.
    """
    logger.warning(f"YouTube URL expansion not implemented. URL: {url}")
    return []