import pytest
import asyncio
from pathlib import Path

from input_parsers import parse_csv, expand_spotify_url, expand_youtube_url
from models import Track

@pytest.fixture
def csv_file(tmp_path):
    content = """artist,title,album
Artist A,Song 1,Album X
Artist B,Song 2,Album Y
Artist C,,Album Z
"""
    file_path = tmp_path / "test.csv"
    file_path.write_text(content)
    return file_path

def test_parse_csv_valid(csv_file):
    tracks = parse_csv(str(csv_file))
    assert len(tracks) == 2 # One row skipped due to missing title
    assert isinstance(tracks[0], Track)
    assert tracks[0].artist == "Artist A"
    assert tracks[0].title == "Song 1"
    assert tracks[0].album == "Album X"

def test_parse_csv_missing_file():
    tracks = parse_csv("non_existent.csv")
    assert len(tracks) == 0

def test_parse_csv_missing_fields(tmp_path):
    content = "artist,album\nArtist D,Album W\n"
    file_path = tmp_path / "bad.csv"
    file_path.write_text(content)
    tracks = parse_csv(str(file_path))
    assert len(tracks) == 0 # Should skip due to missing title

@pytest.mark.asyncio
async def test_expand_spotify_url_placeholder():
    tracks = await expand_spotify_url("https://open.spotify.com/playlist/123")
    assert len(tracks) == 0