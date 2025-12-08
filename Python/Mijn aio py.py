import importlib
import subprocess
import sys
import asyncio
import threading
import os
# Do not hardcode TCL/TK library locations here — this can cause version
# conflicts when the running Python has a different Tcl/Tk version. Rely on
# the Tcl/Tk that is bundled with the active Python interpreter. If you
# really need to override these, set the environment variables externally
# to paths that match the interpreter's Tcl/Tk versions.
# os.environ['TCL_LIBRARY'] = r"C:\Program Files\Python313\tcl\tcl8.6"
# os.environ['TK_LIBRARY'] = r"C:\Program Files\Python313\tcl\tk8.6"
import tkinter as tk
from tkinter import ttk, messagebox
import keyring
import logging
from typing import Optional, List

import config
from aioslsk_adapter import AioSlskAdapter
from models import Track, Job
from job_queue import JobQueue
from download_worker import DownloadWorker

def ensure_package(package, import_name=None):
    """(Deprecated) Check if a package is installed, and install it if it's missing."""
    try:
        importlib.import_module(import_name or package)
    except ImportError:
        print(f"Required package '{package}' not found. Attempting to install...")
        # Installation logic removed in favor of requirements.txt
        print(f"ERROR: Please install dependencies using 'pip install -r requirements.txt'", file=sys.stderr)
        sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# GUI App
class SoulseekApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Soulseek Downloader")
        self.root.geometry("600x600")

        # Load configuration
        self.config = config.load_config()

        # Backend components
        self.adapter: Optional[AioSlskAdapter] = None
        self.job_queue: Optional[JobQueue] = None
        self.download_worker: Optional[DownloadWorker] = None

        # Create a dedicated asyncio loop in a background thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        # To store the actual search result objects
        self.search_results = []
        # To hold the current download task for cancellation
        self.download_task = None

        self._setup_ui()

        # If a password exists in the keyring or config, prefill it and mark
        # the remember checkbox so the user knows credentials are stored.
        try:
            existing_pw = config.retrieve_password(self.config.username) if self.config.username else ""
            if existing_pw:
                self.password_entry.insert(0, existing_pw)
                self.remember_var.set(True)
        except Exception:
            pass

        self._init_backend()

    def _setup_ui(self):
        # Login Frame
        self.login_frame = tk.Frame(self.root)
        self.login_frame.pack(pady=20)

        tk.Label(self.login_frame, text="Username").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.username_entry = tk.Entry(self.login_frame, width=30)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)
        self.username_entry.insert(0, self.config.username)

        tk.Label(self.login_frame, text="Password").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.password_entry = tk.Entry(self.login_frame, show="*", width=30)
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)

        # Remember password checkbox (controls whether plaintext fallback is used)
        self.remember_var = tk.BooleanVar(value=False)
        self.remember_chk = ttk.Checkbutton(self.login_frame, text="Remember password", variable=self.remember_var)
        self.remember_chk.grid(row=2, columnspan=2, pady=(0, 5))

        self.login_btn = ttk.Button(self.login_frame, text="Login", command=self.login)
        self.login_btn.grid(row=3, columnspan=2, pady=10)

        # Search Frame
        self.search_frame = tk.Frame(self.root)
        # self.search_frame.pack(pady=10) # Packed after login

        tk.Label(self.search_frame, text="Search Query").pack(pady=(0, 5))
        self.search_entry = ttk.Entry(self.search_frame, width=50)
        self.search_entry.pack(pady=(0, 5))
        self.search_btn = ttk.Button(self.search_frame, text="Search", command=self.search)
        self.search_btn.pack(pady=(0, 10))

        # Results
        self.results_box = tk.Listbox(self.search_frame, width=80, height=10)
        self.results_box.pack(pady=(0, 10))
        self.download_btn = ttk.Button(self.search_frame, text="Add to Download Queue", command=self.download_selected, state=tk.DISABLED)
        self.download_btn.pack(pady=(0, 5))
        self.results_box.bind("<<ListboxSelect>>", self.on_result_select)

        # Progress
        self.progress_label = tk.Label(self.search_frame, text="Download Progress:")
        self.progress_label.pack(pady=(10, 0))
        self.progress = ttk.Progressbar(self.search_frame, length=400, mode='determinate')
        self.progress.pack(pady=(0, 10))

        # Cancel button
        self.cancel_btn = ttk.Button(self.search_frame, text="Cancel Download", command=self.cancel_download, state=tk.DISABLED)
        self.cancel_btn.pack(pady=(0, 10))

    def _init_backend(self):
        self.adapter = AioSlskAdapter(self.config)
        self.job_queue = JobQueue(self.config)
        self.download_worker = DownloadWorker(
            adapter=self.adapter,
            job_queue=self.job_queue,
            config=self.config,
            progress_callback=self._update_job_progress,
            status_callback=self._update_job_status
        )
        self.run_async(self.download_worker.run())

    def _update_job_progress(self, job: Job, progress: float):
        """Callback to update GUI progress bar for a specific job."""
        self.root.after(0, lambda: self.progress.config(value=progress * 100))
        logger.debug(f"Job {job.id} progress: {progress:.2f}")

    def _update_job_status(self, job: Job, status_text: str):
        """Callback to update GUI status for a specific job."""
        logger.info(f"Job {job.id} status: {status_text}")

    def login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()

        if not username or not password:
            messagebox.showerror("Error", "Please enter username and password")
            return

        # Save username and password (prefer keyring). Only allow plaintext
        # fallback to config when the user explicitly checks "Remember password".
        saved = config.save_credentials(
            username,
            password,
            self.config,
            use_keyring=True,
            fallback_to_config=self.remember_var.get()
        )

        if not saved:
            messagebox.showwarning("Warning", "Password was not saved to keyring or config.")
        elif self.remember_var.get():
            messagebox.showinfo("Saved", "Password stored (keyring or config) as requested.")

        # Async login
        self.run_async(self.async_login(username, password))

    def run_async(self, coro):
        """Schedule an async task safely from Tkinter callbacks.""" # pragma: no cover
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def async_login(self, username, password): # pragma: no cover
        try:
            await self.adapter.connect()
            def on_success():
                messagebox.showinfo("Success", f"Logged in as {username}")
                self.login_frame.pack_forget()
                self.search_frame.pack(pady=10)
            self.root.after(0, on_success)
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror("Login Failed", str(e)))

    def search(self): # pragma: no cover
        query = self.search_entry.get()
        if not query:
            messagebox.showerror("Error", "Enter a search term")
            return
        self.run_async(self.async_search(query))

    def _update_search_results_gui(self, results: List[Track]): # pragma: no cover
        self.search_results.clear()
        self.results_box.delete(0, tk.END)
        self.search_results.extend(results)
        for result in self.search_results:
            display_text = f"{result.username or 'N/A'} - {result.filename} ({result.size or 'N/A'} bytes, {result.bitrate or 'N/A'} kbps)"
            self.results_box.insert(tk.END, display_text)
        self.download_btn.config(state=tk.DISABLED) # Disable until selection

    async def async_search(self, query): # pragma: no cover
        # The adapter now manages its connection state internally.
        # A check for self.adapter is still good practice.
        if not self.adapter:
            self.root.after(0, lambda: messagebox.showerror("Error", "Not connected to Soulseek. Please log in."))
            return
        try:
            results = await self.adapter.search(query)
            self.root.after(0, lambda: self._update_search_results_gui(results))
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror("Search Error", str(e)))

    def download_selected(self): # pragma: no cover
        selection = self.results_box.curselection()
        if not selection:
            messagebox.showerror("Error", "Select a file to download")
            return

        selected_index = selection[0]
        if 0 <= selected_index < len(self.search_results):
            track_to_download = self.search_results[selected_index]
            if self.job_queue:
                self.job_queue.enqueue(track_to_download)
                messagebox.showinfo("Download Queued", f"'{track_to_download.title}' added to download queue.")
            else:
                messagebox.showerror("Error", "Job queue not initialized.")
        else:
            messagebox.showerror("Error", "Invalid selection. Please search again.")

    def on_result_select(self, event): # pragma: no cover
        self.download_btn.config(state=tk.NORMAL if self.results_box.curselection() else tk.DISABLED)

    def cancel_download(self): # pragma: no cover
        # This will cancel the currently active download task in the worker
        if self.download_worker:
            # A more robust implementation would cancel a specific job by its ID.
            # For now, we can stop the current download if one is active.
            # This is a conceptual change; DownloadWorker would need a `cancel_current_job` method.
            # self.download_worker.cancel_current_job()
            messagebox.showwarning("Cancellation", "Cancellation logic needs to be implemented in DownloadWorker.")
        else:
            messagebox.showinfo("Cancellation", "No active download to cancel.")

    def on_closing(self): # pragma: no cover
        """Handle window closing event."""
        async def shutdown():
            logger.info("Shutting down...")
            if self.download_worker:
                await self.download_worker.stop()
            if self.adapter:
                await self.adapter.disconnect()
            # No async close for job_queue, assuming it's synchronous
            self.loop.call_soon_threadsafe(self.loop.stop)

        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            # Disable the window to prevent user interaction during shutdown
            self.root.protocol("WM_DELETE_WINDOW", lambda: None) # Prevent closing again
            self.run_async(shutdown()).add_done_callback(lambda _: self.root.destroy())

# Run GUI
def main(): # pragma: no cover
    root = tk.Tk()
    app = SoulseekApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__": # pragma: no cover
    main()
