import asyncio
import logging
from pathlib import Path
from queue import Queue
from typing import AsyncIterator, List, Optional

import config
from config import Config
from models import Track
# Use direct imports for the stable aioslsk PyPI API.
from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings
# Import only the events that are guaranteed to exist in the stable PyPI release.
# And the commands necessary for a robust search implementation.
from aioslsk.events import (
    TransferProgressEvent,
    TransferAddedEvent,
    SearchResultsEvent,
    SearchResponse,
)
from aioslsk.commands import SearchCommand

logger = logging.getLogger(__name__)

class AioSlskAdapter:
    """
    Adapter for interacting with the aioslsk library (stable PyPI API).
    """
    def __init__(self, config: Config, event_queue: Optional[Queue] = None):
        self.config = config
        self._client: Optional[SoulSeekClient] = None
        self._username = config.username
        self._download_progress_queues: dict[str, asyncio.Queue] = {}
        self._event_listeners_registered = False
        self.event_queue = event_queue
        self._search_future: Optional[asyncio.Future] = None
        self._password: Optional[str] = None

    async def connect(self, password: Optional[str] = None) -> None:
        """
        Connects and authenticates to the Soulseek network using the stable PyPI API.
        """
        if self._client:
            logger.info("Already connected to Soulseek.")
            return

        if not self.config.username:
            raise ValueError("Username not set in config.")

        # Use provided password or retrieve from storage
        if password:
            self._password = password
        else:
            self._password = config.retrieve_password(self.config.username)

        if not self._password:
            raise ValueError("Password missing. Please log in via GUI.")

        logger.info("Connecting to Soulseek...")

        # Build settings object exactly as the aioslsk documentation specifies
        settings = Settings(
            credentials=CredentialsSettings(username=self.config.username, password=self._password)
        )

        # Instantiate client with settings
        # Network parameters like listen_port and use_upnp are passed to the client, not the settings.
        # For aioslsk 1.6.1, network settings are not passed during initialization.
        self._client = SoulSeekClient(settings)

        # Register event listeners BEFORE connecting to ensure we catch all events
        if not self._event_listeners_registered:
            self._register_event_listeners()

        # The full, correct login sequence with a timeout
        try:
            logger.info(f"Attempting connection with a {self.config.connect_timeout}s timeout...")
            async with asyncio.timeout(self.config.connect_timeout):
                await self._client.start()
                await self._client.login()
            logger.info("✅ Connected and Logged in to Soulseek.")
            # Manually put the success event into the queue, replacing the old event handler
            if self.event_queue:
                self.event_queue.put(("connection_status", {"status": "connected", "username": self.config.username}))

        except asyncio.TimeoutError:
            logger.error("❌ Connection timed out! The server is not responding or login is taking too long.")
            logger.error("POSSIBLE FIXES:")
            logger.error("1. Check if Windows Firewall is blocking 'python.exe'.")
            logger.error("2. Verify your internet connection and that the Soulseek server is online.")
            logger.error("3. Try increasing the 'connect_timeout' value in your config.ini file.")
            raise
        except Exception as e:
            logger.error(f"❌ LOGIN/CONNECTION ERROR: {e}")
            logger.error(traceback.format_exc())
            raise

    async def disconnect(self) -> None:
        """Disconnects from the Soulseek network."""
        if self._client:
            logger.info("Disconnecting from Soulseek...")
            await self._client.stop()
            self._client = None
            logger.info("Disconnected from Soulseek.")

    async def search(self, query: str, wait_for_results: bool = False) -> List[Track]:
        """
        Searches for tracks on Soulseek using the command-based API.
        This method is now event-driven. It initiates the search and returns
        an empty list. The actual results are sent to the GUI via the event queue.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek. Please log in.")
        logger.info(f"Searching for: {query}")

        # For workers that need direct results
        if wait_for_results:
            self._search_future = asyncio.get_running_loop().create_future()

        await self._client.execute(SearchCommand(query))

        # If waiting, block until the event handler sets the result.
        return await self._search_future if wait_for_results and self._search_future else []

    async def download(self, username: str, filename: str, output_path: Path) -> Optional[str]:
        """
        Initiates a file download using the high-level API.
        Progress and completion are handled by event listeners.
        Returns the transfer_id.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Downloading '{filename}' from '{username}' to '{output_path}'")
        transfer = await self._client.download(username=username, filename=filename, output_file=output_path)
        return transfer.id if transfer else None

    def get_transfer(self, transfer_id: str):
        """
        Retrieves a transfer object by its ID from the client's internal list.
        """
        if self._client and hasattr(self._client, 'transfers'):
            for transfer in self._client.transfers:
                if transfer.id == transfer_id:
                    return transfer
        return None

    async def cancel_download(self, transfer_id: str) -> None:
        """
        Cancels a specific download. The PyPI API does not have a direct cancel method,
        so this is a placeholder. Cancellation is best handled by stopping the worker.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.warning(f"Cancellation for transfer '{transfer_id}' requested, but not directly supported by this aioslsk version.")

    def _register_event_listeners(self):
        """Register handlers for download-related events on the aioslsk event bus."""
        if not self._client or not hasattr(self._client, 'on') or not self.event_queue:
            logger.debug("Skipping event listener registration (no client, 'on' method, or event_queue).")
            return

        logger.info("Registering aioslsk event listeners.")

        @self._client.on(SearchResponse)
        async def on_search_results(event: SearchResponse):
            tracks = [
                Track(artist=r.artist, title=r.title, album=r.album, filename=r.filename,
                      size=r.size, username=r.username, bitrate=r.bitrate, metadata={})
                for r in event.results
            ]
            if self._search_future and not self._search_future.done():
                self._search_future.set_result(tracks)
            if self.event_queue:
                self.event_queue.put(("search_results", tracks))

        @self._client.on(TransferAddedEvent) # type: ignore
        async def on_transfer_added(event: TransferAddedEvent):
            self.event_queue.put(("transfer_added", event.transfer))

        @self._client.on(TransferProgressEvent) # type: ignore
        async def on_transfer_progress(event: TransferProgressEvent):
            self.event_queue.put(("transfer_progress", event.transfer))

            # The stable PyPI API does not have distinct finished/failed events.
            # We detect termination by checking the transfer's state.
            if event.transfer.state.name == 'FINISHED':
                self.event_queue.put(("transfer_finished", event.transfer))
            elif event.transfer.state.name == 'FAILED':
                error_msg = getattr(event.transfer, 'error', 'Unknown error')
                self.event_queue.put(("transfer_failed", {"transfer": event.transfer, "error": error_msg}))

        self._event_listeners_registered = True