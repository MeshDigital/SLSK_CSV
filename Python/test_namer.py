import pytest
from models import Track
from namer import format_filename

def test_format_filename_basic():
    track = Track(artist="Test Artist", title="Test Title", album="Test Album", filename="original.mp3", size=100, username="user")
    template = "{artist} - {title}.{ext}"
    assert format_filename(track, template) == "Test Artist - Test Title.mp3"

def test_format_filename_with_album():
    track = Track(artist="Test Artist", title="Test Title", album="Test Album", filename="original.flac", size=100, username="user")
    template = "{artist} - {album} - {title}.{ext}"
    assert format_filename(track, template) == "Test Artist - Test Album - Test Title.flac"

def test_format_filename_sanitization():
    track = Track(artist="Bad/Artist", title="Bad:Title?", album="Bad*Album", filename="bad.mp3", size=100, username="user")
    template = "{artist} - {title}.{ext}"
    assert format_filename(track, template) == "Bad_Artist - Bad_Title_.mp3"

def test_format_filename_missing_metadata():
    track = Track(artist=None, title="Only Title", album=None, filename="only.mp3", size=100, username="user")
    template = "{artist} - {title} ({album}).{ext}"
    assert format_filename(track, template) == "Unknown Artist - Only Title (Unknown Album).mp3"

def test_format_filename_custom_ext():
    track = Track(artist="Artist", title="Title", album="Album", filename="song.wav", size=100, username="user")
    template = "{artist} - {title}.{ext}"
    assert format_filename(track, template) == "Artist - Title.wav"