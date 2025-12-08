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
import queue
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
        self.root.geometry("800x700")

        # Load configuration
        self.config = config.load_config()

        # Backend components
        self.adapter: Optional[AioSlskAdapter] = None
        self.job_queue: Optional[JobQueue] = None
        self.download_worker: Optional[DownloadWorker] = None

        # Thread-safe queue for communication between asyncio loop and Tkinter
        self.gui_queue = queue.Queue()

        # Create a dedicated asyncio loop in a background thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        # To store the actual search result objects
        self.search_results = []
        # To store transfer IDs for active downloads
        self.active_downloads = {} # {transfer_id: listbox_item_id}

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
        self.root.after(100, self.poll_gui_queue)

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

        tk.Label(self.login_frame, text="Listen Port").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.port_entry = tk.Entry(self.login_frame, width=30)
        self.port_entry.grid(row=2, column=1, padx=5, pady=5)
        self.port_entry.insert(0, str(self.config.listen_port))

        # Remember password checkbox (controls whether plaintext fallback is used)
        self.remember_var = tk.BooleanVar(value=False)
        self.remember_chk = ttk.Checkbutton(self.login_frame, text="Remember password", variable=self.remember_var, takefocus=0)
        self.remember_chk.grid(row=3, column=0, sticky="w", padx=5)

        # UPnP checkbox
        self.upnp_var = tk.BooleanVar(value=self.config.use_upnp)
        self.upnp_chk = ttk.Checkbutton(self.login_frame, text="Use UPnP", variable=self.upnp_var, takefocus=0)
        self.upnp_chk.grid(row=3, column=1, sticky="w", padx=5)

        self.login_btn = ttk.Button(self.login_frame, text="Login", command=self.login)
        self.login_btn.grid(row=5, columnspan=2, pady=10)

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
        self.results_box.bind("<<ListboxSelect>>", lambda e: self.download_btn.config(state=tk.NORMAL))

        # Downloads Frame
        self.downloads_frame = tk.Frame(self.root)
        # self.downloads_frame.pack(pady=10) # Packed after login

        tk.Label(self.downloads_frame, text="Active Downloads").pack(pady=(10, 5))

        # Using a Treeview for a structured download list
        self.downloads_tree = ttk.Treeview(
            self.downloads_frame,
            columns=("filename", "user", "progress", "status"),
            show="headings"
        )
        self.downloads_tree.heading("filename", text="Filename")
        self.downloads_tree.heading("user", text="User")
        self.downloads_tree.heading("progress", text="Progress")
        self.downloads_tree.heading("status", text="Status")
        self.downloads_tree.column("filename", width=300)
        self.downloads_tree.column("user", width=100)
        self.downloads_tree.column("progress", width=100, anchor="center")
        self.downloads_tree.column("status", width=100, anchor="center")
        self.downloads_tree.pack(pady=(0, 10), fill="both", expand=True)

        self.cancel_btn = ttk.Button(self.downloads_frame, text="Cancel Selected Download", command=self.cancel_selected_download, state=tk.DISABLED)
        self.cancel_btn.pack(pady=(0, 10))
        self.downloads_tree.bind("<<TreeviewSelect>>", lambda e: self.cancel_btn.config(state=tk.NORMAL))

        # Status Bar
        self.status_bar = tk.Label(self.root, text="Status: Disconnected", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _init_backend(self):
        self.adapter = AioSlskAdapter(self.config, self.gui_queue)
        self.job_queue = JobQueue(self.config)
        # The worker is now less critical for direct GUI feedback but still manages the job queue
        self.download_worker = DownloadWorker(
            adapter=self.adapter,
            job_queue=self.job_queue,
            config=self.config,
            progress_callback=lambda job, progress: None, # GUI is now event-driven
            status_callback=lambda job, status: None, # GUI is now event-driven
        )
        self.run_async(self.download_worker.run())

    def poll_gui_queue(self):
        """Poll the GUI queue for events from the backend and update the UI."""
        try:
            while True:
                event_type, data = self.gui_queue.get_nowait()
                if event_type == "connection_status":
                    if data["status"] == "connected":
                        self.status_bar.config(text=f"Status: Connected as {data['username']}")
                        messagebox.showinfo("Success", f"Logged in as {data['username']}")
                        self.login_frame.pack_forget()
                        self.search_frame.pack(pady=10)
                        self.downloads_frame.pack(pady=10, fill="both", expand=True)
                    else:
                        self.status_bar.config(text=f"Status: Connection Failed - {data['reason']}")
                        messagebox.showerror("Login Failed", data['reason'])
                elif event_type == "search_results":
                    self._update_search_results_gui(data)
                elif event_type == "transfer_added":
                    self._handle_transfer_added(data)
                elif event_type == "transfer_progress":
                    self._handle_transfer_progress(data)
                elif event_type == "transfer_finished" or event_type == "transfer_failed":
                    self._handle_transfer_terminated(data, event_type)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.poll_gui_queue)

    def _update_search_results_gui(self, results: List[Track]): # pragma: no cover
        """Handles the 'search_results' event from the queue."""
        self.search_results.extend(results)
        for result in results:
            display_text = f"{result.username or 'N/A'} - {result.filename} ({result.size or 'N/A'} bytes, {result.bitrate or 'N/A'} kbps)"
            self.results_box.insert(tk.END, display_text)
        self.status_bar.config(text=f"Status: Found {len(self.search_results)} results.")

    def _handle_transfer_added(self, transfer):
        """Add a new transfer to the downloads treeview."""
        if transfer.id in self.active_downloads:
            return
        item_id = self.downloads_tree.insert(
            "", "end", iid=transfer.id,
            values=(transfer.filename, transfer.username, "0.00%", transfer.state.name)
        )
        self.active_downloads[transfer.id] = item_id

    def _handle_transfer_progress(self, transfer):
        """Update the progress of an existing transfer in the treeview."""
        if transfer.id in self.active_downloads:
            # Ensure progress is a float between 0 and 1
            progress_val = getattr(transfer, 'progress', 0.0)
            progress_str = f"{progress_val:.2%}"
            self.downloads_tree.set(transfer.id, "progress", progress_str)
            self.downloads_tree.set(transfer.id, "status", transfer.state.name)

    def _handle_transfer_terminated(self, data, event_type):
        """Update a transfer's status to FINISHED or FAILED."""
        transfer = data if event_type == "transfer_finished" else data["transfer"]
        error = data.get("error") if event_type == "transfer_failed" else None

        if transfer.id in self.active_downloads:
            if event_type == "transfer_finished":
                self.downloads_tree.set(transfer.id, "progress", "100.00%")
                self.downloads_tree.set(transfer.id, "status", "COMPLETED")
            else:
                self.downloads_tree.set(transfer.id, "status", f"FAILED: {error}")
            # Optionally remove from active downloads dict after a delay or leave it
            # For now, we leave it to show the final status.
            # If you want to clean up, you could do:
            # self.active_downloads.pop(transfer.id, None)
            
            # Find the job associated with this transfer and signal its completion to the worker
            if self.job_queue and self.download_worker:
                job = self.job_queue.find_by_transfer_id(transfer.id)
                if job and job.id:
                    logger.info(f"Signaling completion for job {job.id} associated with transfer {transfer.id}")
                    self.download_worker.signal_job_completion(job.id)

    def login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()

        if not username or not password:
            messagebox.showerror("Error", "Please enter username and password")
            return
        
        # Update and save listen port from the UI
        try:
            new_port = int(self.port_entry.get())
            if self.config.listen_port != new_port:
                self.config.listen_port = new_port
                config.save_config(self.config)
                logger.info(f"Listen port updated to {new_port} and saved.")
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Listen port must be a valid number.")
            return

        # Update and save UPnP setting from the UI
        if self.config.use_upnp != self.upnp_var.get():
            self.config.use_upnp = self.upnp_var.get()
            config.save_config(self.config)
            logger.info(f"UPnP setting updated to {self.config.use_upnp} and saved.")
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
        
        self.status_bar.config(text="Status: Connecting...")
        self.run_async(self.adapter.connect(password=password))

    def run_async(self, coro):
        """Schedule an async task safely from Tkinter callbacks.""" # pragma: no cover
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def search(self): # pragma: no cover
        query = self.search_entry.get()
        if not query:
            messagebox.showerror("Error", "Enter a search term")
            return
        self.run_async(self.async_search(query))

    async def async_search(self, query): # pragma: no cover
        if not self.adapter:
            self.root.after(0, lambda: messagebox.showerror("Error", "Not connected to Soulseek. Please log in."))
            return
        try:
            self.results_box.delete(0, tk.END)
            self.search_results.clear()
            self.status_bar.config(text=f"Status: Searching for '{query}'...")
            await self.adapter.search(query) # Results will arrive via the queue
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

    def cancel_selected_download(self): # pragma: no cover
        """Cancel the download selected in the treeview."""
        selected_items = self.downloads_tree.selection()
        if not selected_items:
            messagebox.showerror("Error", "Select a download to cancel.")
            return

        transfer_id = selected_items[0]
        if self.adapter:
            self.run_async(self.adapter.cancel_download(transfer_id))
            self.downloads_tree.set(transfer_id, "status", "CANCELLING")
            messagebox.showinfo("Cancellation", f"Sent cancellation request for {transfer_id}.")
        else:
            messagebox.showerror("Error", "Adapter not available to cancel download.")

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
            self.status_bar.config(text="Status: Shutting down...")
            future = self.run_async(shutdown())
            # Use add_done_callback to ensure the GUI is destroyed only after
            # the async shutdown tasks are complete.
            future.add_done_callback(lambda _: self.root.destroy())

# Run GUI
def main(): # pragma: no cover
    root = tk.Tk()
    app = SoulseekApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__": # pragma: no cover
    main()
