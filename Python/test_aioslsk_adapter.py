import asyncio
import keyring
import logging
from typing import AsyncIterator, Optional, List, Literal
from pathlib import Path

from models import Track
from config import Config

# Conditional imports based on aioslsk mode
try:
    # PyPI mode imports
    from aioslsk.client import SoulSeekClient
except ImportError:
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from aioslsk_adapter import AioSlskAdapter
from config import Config
from models import Track

# Mock the aioslsk.client.SoulSeekClient
# This needs to be done before aioslsk_adapter tries to import it
pytest_plugins = ('pytest_asyncio',)

class MockSoulSeekClient:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.connected = False
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.search = AsyncMock(return_value=[
            MagicMock(artist="Artist1", title="Title1", album="Album1", filename="Artist1 - Title1.mp3", size=1000, username="user1", bitrate=320),
            MagicMock(artist="Artist2", title="Title2", album="Album2", filename="Artist2 - Title2.flac", size=2000, username="user2", bitrate=1000),
        ])
        self.download = self._mock_download_generator()

    async def _mock_download_generator(self):
        # Simulate progress
        yield 0.0
        await asyncio.sleep(0.01)
        yield 0.5
        await asyncio.sleep(0.01)
        yield 1.0

# Replace the actual SoulSeekClient with our mock
    SoulSeekClient = None # type: ignore

@pytest.fixture
def mock_config():
    cfg = Config(username="testuser", aioslsk_mode="pypi")
    return cfg

@pytest.fixture
def mock_keyring(monkeypatch):
    mock_get_password = MagicMock(return_value="testpass")
    monkeypatch.setattr(keyring, "get_password", mock_get_password)
    return mock_get_password

@pytest.mark.asyncio
async def test_adapter_connect_pypi_mode(mock_config, mock_keyring):
    adapter = AioSlskAdapter(mock_config)
    await adapter.connect()

    mock_keyring.assert_called_once_with("SoulseekApp", "testuser")
    assert adapter._client is not None
    adapter._client.connect.assert_called_once() # type: ignore
    assert adapter._client.username == "testuser" # type: ignore
    assert adapter._client.password == "testpass" # type: ignore

@pytest.mark.asyncio
async def test_adapter_disconnect_pypi_mode(mock_config, mock_keyring):
    adapter = AioSlskAdapter(mock_config)
    await adapter.connect() # Ensure client is initialized
    await adapter.disconnect()

    assert adapter._client is None
    # Check if disconnect was called on the mock client before it was set to None
    assert MockSoulSeekClient.disconnect.called # type: ignore

@pytest.mark.asyncio
async def test_adapter_search_pypi_mode(mock_config, mock_keyring):
    adapter = AioSlskAdapter(mock_config)
    await adapter.connect()

    query = "test query"
    results = await adapter.search(query)

    adapter._client.search.assert_called_once_with(query) # type: ignore
    assert len(results) == 2
    assert isinstance(results[0], Track)
    assert results[0].title == "Title1"
    assert results[1].filename == "Artist2 - Title2.flac"

@pytest.mark.asyncio
async def test_adapter_download_pypi_mode(mock_config, mock_keyring):
    adapter = AioSlskAdapter(mock_config)
    await adapter.connect()

    username = "testuser"
    filename = "testfile.mp3"
    output_path = Path("downloads/testfile.mp3")

    progress_updates = []
    async for progress in adapter.download(username, filename, output_path):
        progress_updates.append(progress)

    adapter._client.download.assert_called_once_with(username=username, filename=filename, output_file=output_path) # type: ignore
    assert progress_updates == [0.0, 0.5, 1.0]

@pytest.mark.asyncio
async def test_adapter_github_mode_not_implemented(mock_keyring):
    config_github = Config(username="testuser", aioslsk_mode="github")
    adapter = AioSlskAdapter(config_github)
    with pytest.raises(NotImplementedError, match="GitHub \\(command API\\) mode requires a specific aioslsk version"):
        await adapter.connect()
try:
    # GitHub mode (command API) imports - these are NOT in PyPI 1.6.1
    from aioslsk.settings import Settings, CredentialsSettings
    from aioslsk.commands import SearchCommand, DownloadFileCommand
    GITHUB_MODE_AVAILABLE = True
except ImportError:
    Settings = None # type: ignore
    CredentialsSettings = None # type: ignore
    SearchCommand = None # type: ignore
    DownloadFileCommand = None # type: ignore
    GITHUB_MODE_AVAILABLE = False

logger = logging.getLogger(__name__)

class AioSlskAdapter:
    """
    Adapter for interacting with the aioslsk library, supporting different API modes.
    """
    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[SoulSeekClient] = None
        self._username = config.username
        self._password: Optional[str] = None

    async def connect(self) -> None:
        """
        Connects to the Soulseek network.
        Retrieves password from keyring if available.
        """
        if self._client and self._client.connected:
            logger.info("Already connected to Soulseek.")
            return

        if not self._username:
            raise ValueError("Username not set in config.")

        self._password = keyring.get_password("SoulseekApp", self._username)
        if not self._password:
            raise ValueError("Password not found in keyring. Please log in via GUI.")

        if self.config.aioslsk_mode == "pypi":
            if SoulSeekClient is None:
                raise ImportError("SoulSeekClient not found for PyPI mode. Is aioslsk installed correctly?")
            logger.info("Connecting to Soulseek in PyPI mode...")
            self._client = SoulSeekClient(username=self._username, password=self._password)
            await self._client.connect()
        elif self.config.aioslsk_mode == "github":
            if not GITHUB_MODE_AVAILABLE:
                raise NotImplementedError(
                    "GitHub (command API) mode requires a specific aioslsk version "
                    "not available on PyPI. Please install from GitHub master."
                )
            # Placeholder for GitHub mode connection
            logger.info("Connecting to Soulseek in GitHub (command API) mode (placeholder)...")
            settings = Settings(credentials=CredentialsSettings(username=self._username, password=self._password)) # type: ignore
            self._client = SoulSeekClient(settings) # type: ignore
            await self._client.start()
            await self._client.login()
        else:
            raise ValueError(f"Unknown aioslsk_mode: {self.config.aioslsk_mode}")
        logger.info("Connected to Soulseek.")

    async def disconnect(self) -> None:
        """Disconnects from the Soulseek network."""
        if self._client:
            logger.info("Disconnecting from Soulseek...")
            if self.config.aioslsk_mode == "pypi":
                await self._client.disconnect()
            elif self.config.aioslsk_mode == "github":
                await self._client.stop() # type: ignore
            self._client = None
            logger.info("Disconnected from Soulseek.")

    async def search(self, query: str) -> List[Track]:
        """
        Searches for tracks on Soulseek.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Searching for: {query}")
        if self.config.aioslsk_mode == "pypi":
            results = await self._client.search(query)
            return [
                Track(
                    artist=r.artist, title=r.title, album=r.album, filename=r.filename,
                    size=r.size, username=r.username, bitrate=r.bitrate, metadata={}
                ) for r in results
            ]
        elif self.config.aioslsk_mode == "github":
            # Placeholder for GitHub mode search
            logger.warning("GitHub mode search is a placeholder.")
            response = await self._client.execute(SearchCommand(query), response=True) # type: ignore
            return [Track(artist=None, title=None, album=None, filename=r.filename, size=r.size, username=r.username) for r in response.results] # type: ignore
        return []

    async def download(self, username: str, filename: str, output_path: Path) -> AsyncIterator[float]:
        """
        Downloads a file from Soulseek, yielding progress updates (0.0 to 1.0).
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Downloading '{filename}' from '{username}' to '{output_path}'")
        if self.config.aioslsk_mode == "pypi":
            async for progress in self._client.download(username=username, filename=filename, output_file=output_path):
                yield progress
        elif self.config.aioslsk_mode == "github":
            # Placeholder for GitHub mode download
            logger.warning("GitHub mode download is a placeholder. Progress will be 0.0 then 1.0.")
            yield 0.0
            await self._client.execute(DownloadFileCommand(username=username, filename=filename, save_path=output_path), response=True) # type: ignore
            yield 1.0

    async def cancel_download(self, transfer_id: Optional[str] = None) -> None:
        """
        Cancels a specific download.
        Note: aioslsk's high-level API doesn't expose transfer_id for direct cancellation.
        Cancellation is typically handled by cancelling the asyncio task running the download.
        """
        logger.warning("Direct cancellation by transfer_id is not directly supported by aioslsk's high-level API.")
        logger.info("To cancel a download, cancel the asyncio task that initiated it.")