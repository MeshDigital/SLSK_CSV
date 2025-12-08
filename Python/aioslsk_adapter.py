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
    from aioslsk.commands import SearchCommand, DownloadFileCommand, CancelTransferCommand
    from aioslsk.events import TransferProgressEvent, TransferAddedEvent, TransferRemovedEvent
    GITHUB_MODE_AVAILABLE = True
except ImportError:
    Settings = None # type: ignore
    CredentialsSettings = None # type: ignore
    SearchCommand = None # type: ignore
    DownloadFileCommand = None # type: ignore
    CancelTransferCommand = None # type: ignore
    TransferProgressEvent = None # type: ignore
    TransferAddedEvent = None # type: ignore
    TransferRemovedEvent = None # type: ignore
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
        self._download_progress_queues: dict[str, asyncio.Queue] = {}
        self._event_listeners_registered = False
        self._password: Optional[str] = None

    async def connect(self) -> None:
        """
        Connects to the Soulseek network.
        Retrieves password from keyring if available.
        """
        if self._client and self._client.connected:
            logger.info("Already connected to Soulseek.")
            return

        # Ensure we refer to module-level Settings/CredentialsSettings when
        # attempting dynamic imports below. Without this `global` declaration
        # assigning to `Settings` or `CredentialsSettings` would make them
        # local to this function and cause UnboundLocalError when referenced
        # earlier in the function.
        global Settings, CredentialsSettings

        if not self.config.username:
            raise ValueError("Username not set in config.")

        # Retrieve password via config helper (prefer keyring, fallback to config file)
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
                logger.info("Building Settings-based SoulSeekClient (preferred for this aioslsk build)")
                try:
                    creds = CredentialsSettings(username=self.config.username, password=self._password) # type: ignore
                    settings_kwargs = {'credentials': creds}

                    settings_obj = Settings(**settings_kwargs) # type: ignore
                    try:
                        self._client = SoulSeekClient(settings_obj) # type: ignore
                    except TypeError:
                        self._client = SoulSeekClient(settings=settings_obj) # type: ignore

                    if hasattr(self._client, 'start'):
                        start_fn = getattr(self._client, 'start')
                        if inspect.iscoroutinefunction(start_fn):
                            await start_fn()
                        else:
                            start_fn()
                    if hasattr(self._client, 'login'):
                        login_fn = getattr(self._client, 'login')
                        if inspect.iscoroutinefunction(login_fn):
                            logger.debug("Calling await client.login()")
                            await login_fn()
                        else:
                            login_fn()
                except Exception as e:
                    logger.error("Settings-based SoulSeekClient construction failed: %s", e)
                    logger.error(traceback.format_exc())
                    raise
            else:
                # Fallback: try username/password constructor, then no-arg + login
                try:
                    logger.debug("Attempting to instantiate SoulSeekClient with username/password.")
                    self._client = SoulSeekClient(username=self.config.username, password=self._password)
                    if hasattr(self._client, 'connect'):
                        connect_fn = getattr(self._client, 'connect')
                        if inspect.iscoroutinefunction(connect_fn):
                            await connect_fn()
                        else:
                            connect_fn()
                except TypeError as te:
                    logger.info("SoulSeekClient constructor rejected username/password, trying alternate flows: %s", te)
                    logger.debug("Attempting to instantiate SoulSeekClient with no arguments.")
                    try:
                        self._client = SoulSeekClient()
                    except Exception as e:
                        logger.debug("No-arg instantiation failed: %s", e)
                        logger.debug(traceback.format_exc())
                        raise

                    if hasattr(self._client, 'connect'):
                        connect_fn = getattr(self._client, 'connect')
                        try:
                            if inspect.iscoroutinefunction(connect_fn):
                                await connect_fn()
                            else:
                                connect_fn()
                        except Exception as e:
                            logger.warning("connect() call failed: %s", e)

                    if hasattr(self._client, 'login'):
                        login_fn = getattr(self._client, 'login')
                        try:
                            if inspect.iscoroutinefunction(login_fn):
                                await login_fn(self.config.username, self._password)
                            else:
                                login_fn(self.config.username, self._password)
                        except TypeError:
                            if inspect.iscoroutinefunction(login_fn):
                                await login_fn()
                            else:
                                login_fn()
        elif self.config.aioslsk_mode == "github":
            if not GITHUB_MODE_AVAILABLE:
                raise NotImplementedError(
                    "GitHub (command API) mode requires a specific aioslsk version "
                    "not available on PyPI. Please install from GitHub master."
                )
            # Placeholder for GitHub mode connection
            logger.info("Connecting to Soulseek in GitHub (command API) mode (placeholder)...")
            settings = Settings(credentials=CredentialsSettings(username=self.config.username, password=self._password)) # type: ignore
            self._client = SoulSeekClient(settings) # type: ignore
            await self._client.start()
            await self._client.login()
        else:
            raise ValueError(f"Unknown aioslsk_mode: {self.config.aioslsk_mode}")
        logger.info("Connected to Soulseek.")
        # Register event listeners if in GitHub mode and not already done
        if self.config.aioslsk_mode == "github" and not self._event_listeners_registered:
            self._register_event_listeners()
            self._event_listeners_registered = True

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
        global SearchCommand

        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Searching for: {query}")

        # Adapt to the client object's capabilities, not just the config string.
        # Some "pypi" mode clients are instantiated with Settings and require commands.
        if hasattr(self._client, 'search') and callable(getattr(self._client, 'search')):
            logger.debug("Using high-level client.search() method.")
            results = await self._client.search(query)
            return [
                Track(
                    artist=r.artist, title=r.title, album=r.album, filename=r.filename,
                    size=r.size, username=r.username, bitrate=r.bitrate, metadata={}
                ) for r in results
            ]
        elif hasattr(self._client, 'execute') and callable(getattr(self._client, 'execute')):
            logger.debug("Using command-based client.execute(SearchCommand(...)) method.")
            if SearchCommand is None:
                try:
                    commands_mod = importlib.import_module('aioslsk.commands')
                    SearchCommand = getattr(commands_mod, 'SearchCommand', None)
                except ImportError:
                    raise NotImplementedError("Client requires SearchCommand, but 'aioslsk.commands' could not be imported.")
            response = await self._client.execute(SearchCommand(query), response=True) # type: ignore
            if not response or not hasattr(response, 'results'):
                return []
            return [
                Track(
                    artist=r.artist, title=r.title, album=r.album, filename=r.filename,
                    size=r.size, username=r.username, bitrate=r.bitrate, metadata={}
                ) for r in response.results # type: ignore
            ]
        else:
            raise NotImplementedError("The connected aioslsk client has no recognized search method (.search or .execute).")

    async def download(self, username: str, filename: str, output_path: Path) -> tuple[Optional[str], AsyncIterator[float]]:
        """
        Downloads a file from Soulseek, yielding progress updates (0.0 to 1.0).

        Returns a tuple containing:
        - The transfer_id (if available from the API).
        - An async iterator for download progress.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        logger.info(f"Downloading '{filename}' from '{username}' to '{output_path}'")

        # Create a temporary .part file for the download
        tmp_dir = self.config.download_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # The transfer_id will be the definitive name for the temp file
        # but we don't have it yet. The worker will rename it.
        temp_file_path = output_path.with_suffix(output_path.suffix + ".part")

        if self.config.aioslsk_mode == "pypi":
            # The high-level API does not expose a transfer_id before download starts.
            # The async iterator is the source of progress.
            # We must wrap the call to ensure the return type is consistent.
            async def pypi_download_wrapper():
                async for progress in self._client.download(username=username, filename=filename, output_file=temp_file_path):
                    yield progress
            return None, pypi_download_wrapper()

        elif self.config.aioslsk_mode == "github":
            # In GitHub mode, we execute a command and listen for events.
            cmd = DownloadFileCommand(username=username, filename=filename, save_path=temp_file_path) # type: ignore
            response = await self._client.execute(cmd, response=True) # type: ignore
            transfer_id = response.id

            # Create a queue to receive progress updates for this specific transfer
            progress_queue = asyncio.Queue()
            self._download_progress_queues[transfer_id] = progress_queue

            async def progress_iterator():
                try:
                    while True:
                        progress = await progress_queue.get()
                        yield progress
                        if progress >= 1.0:
                            break
                finally:
                    # Clean up the queue when the download is done or cancelled
                    del self._download_progress_queues[transfer_id]

            return transfer_id, progress_iterator()
        else:
            raise ValueError(f"Unknown aioslsk_mode: {self.config.aioslsk_mode}")

    async def cancel_download(self, transfer_id: str) -> None:
        """
        Cancels a specific download.
        """
        if not self._client:
            raise ConnectionError("Not connected to Soulseek.")
        if self.config.aioslsk_mode == "github" and CancelTransferCommand:
            logger.info(f"Cancelling download with transfer_id: {transfer_id}")
            await self._client.execute(CancelTransferCommand(id=transfer_id)) # type: ignore
        else:
            logger.warning("Direct cancellation by transfer_id is not supported in PyPI mode. The worker must cancel the asyncio task.")

    def _register_event_listeners(self):
        """Register handlers for download-related events on the aioslsk event bus."""
        if not self._client or not hasattr(self._client, 'on'):
            return

        @self._client.on(TransferProgressEvent) # type: ignore
        async def on_transfer_progress(event: TransferProgressEvent):
            if event.id in self._download_progress_queues:
                progress = event.transferred_bytes / event.total_bytes if event.total_bytes > 0 else 0.0
                await self._download_progress_queues[event.id].put(progress)