import asyncio
import logging
from typing import AsyncIterator, Optional, List, Literal
from pathlib import Path

from models import Track
from config import Config
import config
import importlib
import sys
import inspect
import traceback
from collections.abc import Coroutine
from queue import Queue
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
    from aioslsk.commands import SearchCommand, DownloadFileCommand, CancelTransferCommand, DownloadFolderCommand
    from aioslsk.events import (TransferProgressEvent, TransferAddedEvent, TransferRemovedEvent,
                                TransferFinishedEvent, TransferFailedEvent, ConnectionEstablishedEvent,
                                ConnectionFailedEvent, SearchResultsEvent, SearchFailedEvent)
    GITHUB_MODE_AVAILABLE = True
except ImportError:
    Settings = None # type: ignore
    CredentialsSettings = None  # type: ignore
    SearchCommand = None # type: ignore
    DownloadFileCommand = None # type: ignore
    CancelTransferCommand = None # type: ignore
    TransferProgressEvent = None # type: ignore
    TransferAddedEvent = None # type: ignore
    TransferRemovedEvent = None # type: ignore
    GITHUB_MODE_AVAILABLE = False
    TransferFinishedEvent = None # type: ignore
    TransferFailedEvent = None # type: ignore
    ConnectionEstablishedEvent = None # type: ignore
    ConnectionFailedEvent = None # type: ignore
    SearchResultsEvent = None # type: ignore
    SearchFailedEvent = None # type: ignore

logger = logging.getLogger(__name__)

class AioSlskAdapter:
    """
    Adapter for interacting with the aioslsk library, supporting different API modes.
    """
    def __init__(self, config: Config, event_queue: Optional[Queue] = None):
        self.config = config
        self._client: Optional[SoulSeekClient] = None
        self._username = config.username
        self._download_progress_queues: dict[str, asyncio.Queue] = {}
        self._event_listeners_registered = False
        self.event_queue = event_queue
        self._password: Optional[str] = None
        self._search_future: Optional[asyncio.Future] = None

    async def connect(self, password: Optional[str] = None) -> None:
        """
        Connects to the Soulseek network.
        Retrieves password from keyring if available.
        """
        if self._client and self._client.connected:
            logger.info("Already connected to Soulseek.")
            return


        if not self.config.username:
            raise ValueError("Username not set in config.")

        # Use the provided password if available, otherwise retrieve it.
        if password:
            self._password = password
        else:
            try:
                self._password = config.retrieve_password(self.config.username, config_file=config.DEFAULT_CONFIG_FILE)
            except Exception as e:
                logger.warning(f"Could not retrieve password for {self.config.username}: {e}")
                self._password = None

        if not self._password:
            raise ValueError("Password not found in keyring or config. Please log in via GUI.")

        if self.config.aioslsk_mode == "pypi":
            if SoulSeekClient is None:
                # Diagnostic logging to help debug import/layout mismatches
                logger.error("SoulSeekClient class not found in imported aioslsk locations.")
                try:
                    mod = importlib.import_module("aioslsk")
                    logger.error(f"aioslsk module found at: {getattr(mod, '__file__', 'unknown')}")
                    logger.error(f"aioslsk contents: {sorted([n for n in dir(mod) if not n.startswith('__')])}")
                except Exception as ie:
                    logger.error(f"Failed to import aioslsk for diagnostics: {ie}")
                logger.error(f"Python executable: {sys.executable}")
                logger.error(f"sys.path: {sys.path}")
                raise ImportError(
                    "SoulSeekClient not found for PyPI mode. Is `aioslsk` installed correctly? "
                    "Try `pip install -U aioslsk` or check the package layout. "
                    "Run `python -c \"import aioslsk; print(dir(aioslsk))\"` to inspect the installed package."
                )
            logger.info("Connecting to Soulseek in PyPI mode...")

            # Ensure download directory exists for Settings that expect paths
            try:
                Path(self.config.download_dir).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.debug("Could not ensure download dir exists, continuing anyway.")

            # Prefer Settings-based construction when available (many builds require it)
            # If the Settings import failed at module import time, try to import it dynamically.
            if not ('Settings' in globals() and Settings is not None and CredentialsSettings is not None):
                try:
                    settings_mod = importlib.import_module('aioslsk.settings')
                    Settings = getattr(settings_mod, 'Settings', None)  # type: ignore
                    CredentialsSettings = getattr(settings_mod, 'CredentialsSettings', None)  # type: ignore
                    logger.info("Dynamically imported aioslsk.settings module for Settings/CredentialsSettings")
                except Exception:
                    Settings = None  # type: ignore
                    CredentialsSettings = None  # type: ignore

            prefers_settings = ('Settings' in globals() and Settings is not None and CredentialsSettings is not None)
            logger.info(f"Settings available: {Settings is not None}, CredentialsSettings available: {CredentialsSettings is not None}")

            if prefers_settings:
                await self._connect_with_settings()
            else:
                await self._connect_with_fallback()

        elif self.config.aioslsk_mode == "github":
            # This mode is now handled by the 'prefers_settings' logic in pypi mode.
            # If a specific github-only flow is needed, it can be re-added,
            # but the current logic is identical.
            raise NotImplementedError("The 'github' aioslsk_mode is deprecated; the adapter now auto-detects API style.")
        else:
            raise ValueError(f"Unknown aioslsk_mode: {self.config.aioslsk_mode}")
        logger.info("Connected to Soulseek.")

    async def _connect_with_settings(self):
        """Connection strategy using the modern Settings-based API."""
        logger.info("Building Settings-based SoulSeekClient (preferred for this aioslsk build)")
        try:
            # Match the successful test script's initialization logic
            settings_obj = Settings(
                credentials=CredentialsSettings(username=self.config.username, password=self._password),
                listen_port=self.config.listen_port,
                use_upnp=self.config.use_upnp
            )
            
            # Use the simpler constructor that just takes the settings object,
            # which is more common in recent versions.
            self._client = SoulSeekClient(settings_obj)

            if not self._event_listeners_registered:
                self._register_event_listeners()

            await self._perform_login_sequence()
            logger.info("Successfully connected and logged in via Settings-based client.")

        except Exception as e:
            logger.error("Settings-based SoulSeekClient connection failed: %s", e)
            logger.error(traceback.format_exc())
            raise

    async def _connect_with_fallback(self):
        """Connection strategy using older username/password or no-arg constructors."""
        logger.info("Attempting fallback connection methods.")
        # Strategy 1: username/password constructor
        try:
            logger.debug("Trying username/password constructor.")
            self._client = SoulSeekClient(username=self.config.username, password=self._password)
            if hasattr(self._client, 'connect'):
                connect_fn = getattr(self._client, 'connect')
                if inspect.iscoroutinefunction(connect_fn):
                    await connect_fn()
                else:
                    connect_fn()
                logger.info("Successfully connected via username/password client.")
                return
        except Exception as e:
            logger.warning("Username/password client instantiation or connect failed: %s", e)
            logger.debug(traceback.format_exc())

        # Strategy 2: no-arg constructor + login with arguments
        try:
            logger.debug("Trying no-arg constructor and login(user, pass).")
            self._client = SoulSeekClient()
            await self._perform_login_sequence(with_creds=True)
            logger.info("Successfully connected and logged in via fallback client.")
            return
        except Exception as e:
            logger.error(f"Failed during fallback connect/login process: {e}")
            logger.error(traceback.format_exc())
            raise

    async def _perform_login_sequence(self, with_creds: bool = False):
        """Handles the start/connect and login calls, which vary between versions."""
        if not self._client:
            raise RuntimeError("Client not initialized before login sequence.")

        async def sequence():
            # Step 1: Start the connection (start() or connect())
            # Prioritize client.start() as it's the modern API's entry point.
            # Some versions have both .start() and .connect(), and .start() is the correct one to use.
            if hasattr(self._client, 'start'):
                start_fn = getattr(self._client, 'start')
                logger.debug("Found client.start() method.")
                if inspect.iscoroutinefunction(start_fn):
                    logger.debug("Calling await client.start()")
                    await start_fn()
                else:
                    logger.debug("Calling client.start()")
                    start_fn()
                logger.debug("client.start() completed.")
            elif hasattr(self._client, 'connect'):
                # Fallback for older versions that only have .connect()
                connect_fn = getattr(self._client, 'connect')
                logger.debug("Found client.connect() method.")
                if inspect.iscoroutinefunction(connect_fn):
                    logger.debug("Calling await client.connect()")
                    await connect_fn()
                else:
                    logger.debug("Calling client.connect()")
                    connect_fn()
                logger.debug("client.connect() completed.")
            else:
                logger.warning("No .start() or .connect() method found on the client.")

            # Step 2: Perform login
            if hasattr(self._client, 'login'):
                login_fn = getattr(self._client, 'login')
                logger.debug("Found client.login() method.")
                if inspect.iscoroutinefunction(login_fn):
                    if with_creds:
                        logger.debug("Calling await client.login(username, password)")
                        await login_fn(self.config.username, self._password)
                    else:
                        logger.debug("Calling await client.login()")
                        await login_fn()
                else:
                    if with_creds:
                        logger.debug("Calling client.login(username, password)")
                        login_fn(self.config.username, self._password)
                    else:
                        logger.debug("Calling client.login()")
                        login_fn()
                logger.debug("client.login() completed.")

        try:
            logger.info(f"Attempting connection with a {self.config.connect_timeout} second timeout...")
            await asyncio.wait_for(sequence(), timeout=self.config.connect_timeout)
            logger.info("✅ Connection and login sequence completed successfully.")
        except asyncio.TimeoutError:
            logger.error("❌ Connection timed out! The server is not responding or login is taking too long.")
            logger.error("POSSIBLE FIXES:")
            logger.error("1. Check if Windows Firewall or another firewall is blocking 'python.exe'.")
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
            if self.config.aioslsk_mode == "pypi":
                await self._client.disconnect()
            elif self.config.aioslsk_mode == "github":
                await self._client.stop() # type: ignore
            self._client = None
            logger.info("Disconnected from Soulseek.")

    async def search(self, query: str, wait_for_results: bool = False) -> List[Track]:
        """
        Searches for tracks on Soulseek.

        If `wait_for_results` is True, this method will wait for the search
        results event and return the tracks directly. This is useful for workers.

        If `wait_for_results` is False (default), it returns an empty list
        immediately, and results are sent to the event_queue. This is for the GUI.
        """
        global SearchCommand

        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Searching for: {query}")

        if wait_for_results:
            if self._search_future and not self._search_future.done():
                raise RuntimeError("Another search operation is already waiting for results.")
            self._search_future = asyncio.get_running_loop().create_future()

        # Adapt to the client object's capabilities, not just the config string.
        # Some "pypi" mode clients are instantiated with Settings and require commands.
        if hasattr(self._client, 'search') and callable(getattr(self._client, 'search')):
            # The high-level search returns results directly, but we want to use the event-based
            # system for consistency in the GUI. We'll still call it, but the results will
            # be handled by the on_search_results event handler.
            logger.debug("Using high-level client.search() method. Results will be sent via event queue.")
            await self._client.search(query)
            if wait_for_results and self._search_future:
                return await self._search_future
            else:
                return []

        elif hasattr(self._client, 'execute') and callable(getattr(self._client, 'execute')):
            logger.debug("Using command-based client.execute(SearchCommand(...)) method.")
            if SearchCommand is None:
                try:
                    commands_mod = importlib.import_module('aioslsk.commands')
                    SearchCommand = getattr(commands_mod, 'SearchCommand', None)
                except ImportError:
                    raise NotImplementedError("Client requires SearchCommand, but 'aioslsk.commands' could not be imported.")
            # For command-based search, we also rely on the SearchResultsEvent.
            # We execute the command but don't wait for a direct response here.
            await self._client.execute(SearchCommand(query)) # type: ignore
            if wait_for_results and self._search_future:
                return await self._search_future
            else:
                return []
        else:
            raise NotImplementedError("The connected aioslsk client has no recognized search method (.search or .execute).")

    async def download(self, username: str, filename: str, output_path: Path) -> Optional[str]:
        """
        Downloads a file from Soulseek, yielding progress updates (0.0 to 1.0).

        Returns a tuple containing:
        - The transfer_id (if available from the API).
        - An async iterator for download progress.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Downloading '{filename}' from '{username}' to '{output_path}'")

        # Both PyPI and GitHub modes can use event-driven downloads.
        # The `download` method on the client is now preferred as it triggers the events.
        if hasattr(self._client, 'download') and callable(getattr(self._client, 'download')):
            logger.debug("Using high-level client.download() method to initiate transfer.")
            # This call initiates the download and returns a Transfer object.
            # The progress and completion will be handled by the event listeners.
            transfer = await self._client.download(username=username, filename=filename, output_file=output_path)
            return transfer.id if transfer else None
        elif hasattr(self._client, 'execute') and callable(getattr(self._client, 'execute')) and DownloadFileCommand:
            logger.debug("Using command-based client.execute(DownloadFileCommand(...)) to initiate transfer.")
            cmd = DownloadFileCommand(username=username, filename=filename, save_path=output_path) # type: ignore
            response = await self._client.execute(cmd, response=True) # type: ignore
            if response and hasattr(response, 'id'):
                return response.id
            return None
        else:
            raise NotImplementedError("The connected aioslsk client has no recognized download method.")


    async def cancel_download(self, transfer_id: str) -> None:
        """
        Cancels a specific download.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        if hasattr(self._client, 'execute') and callable(getattr(self._client, 'execute')) and CancelTransferCommand:
            logger.info(f"Cancelling download with transfer_id: {transfer_id}")
            await self._client.execute(CancelTransferCommand(id=transfer_id)) # type: ignore
        else:
            logger.warning("Direct cancellation by transfer_id is not supported by this aioslsk client.")

    def _register_event_listeners(self):
        """Register handlers for download-related events on the aioslsk event bus."""
        if not self._client or not hasattr(self._client, 'on') or not self.event_queue:
            logger.debug("Skipping event listener registration (no client, 'on' method, or event_queue).")
            return

        logger.info("Registering aioslsk event listeners.")

        @self._client.on(ConnectionEstablishedEvent) # type: ignore
        async def on_connect(event: ConnectionEstablishedEvent):
            self.event_queue.put(("connection_status", {"status": "connected", "username": event.username}))

        @self._client.on(ConnectionFailedEvent) # type: ignore
        async def on_connect_fail(event: ConnectionFailedEvent):
            self.event_queue.put(("connection_status", {"status": "failed", "reason": str(event.exception)}))

        @self._client.on(SearchResultsEvent) # type: ignore
        async def on_search_results(event: SearchResultsEvent):
            tracks = [
                Track(artist=r.artist, title=r.title, album=r.album, filename=r.filename,
                      size=r.size, username=r.username, bitrate=r.bitrate, metadata={})
                for r in event.results
            ]
            # If a future is waiting for these results, fulfill it.
            if self._search_future and not self._search_future.done():
                self._search_future.set_result(tracks)
            # Otherwise, or in addition, send to the GUI queue.
            if self.event_queue:
                self.event_queue.put(("search_results", tracks))

        @self._client.on(TransferAddedEvent) # type: ignore
        async def on_transfer_added(event: TransferAddedEvent):
            self.event_queue.put(("transfer_added", event.transfer))

        @self._client.on(TransferProgressEvent) # type: ignore
        async def on_transfer_progress(event: TransferProgressEvent):
            self.event_queue.put(("transfer_progress", event.transfer))

        @self._client.on(TransferFinishedEvent) # type: ignore
        async def on_transfer_finished(event: TransferFinishedEvent):
            self.event_queue.put(("transfer_finished", event.transfer))

        @self._client.on(TransferFailedEvent) # type: ignore
        async def on_transfer_failed(event: TransferFailedEvent):
            self.event_queue.put(("transfer_failed", {"transfer": event.transfer, "error": event.error}))

        self._event_listeners_registered = True