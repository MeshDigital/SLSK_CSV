import asyncio
import keyring
import logging
from typing import AsyncIterator, Optional, List, Literal
from pathlib import Path

from models import Track
from config import Config

# Conditional imports based on aioslsk mode. Try a few fallbacks and provide
# a clearer error message if the expected class isn't found.
try:
    # Preferred PyPI layout
    from aioslsk.client import SoulSeekClient
except Exception:
    SoulSeekClient = None  # type: ignore
    try:
        # Some package versions may expose a top-level SoulSeekClient
        from aioslsk import SoulSeekClient  # type: ignore
    except Exception:
        SoulSeekClient = None  # type: ignore

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
                # Provide more actionable guidance
                raise ImportError(
                    "SoulSeekClient not found for PyPI mode. Is `aioslsk` installed correctly? "
                    "Try `pip install -U aioslsk` or check the package layout. "
                    "You can also run `python -c \"import aioslsk; print(dir(aioslsk))\"` to inspect the installed package."
                )
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