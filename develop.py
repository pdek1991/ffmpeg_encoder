import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import ttkbootstrap as ttkb
from ttkbootstrap.constants import *

from PIL import Image, ImageTk
import subprocess
import threading
import json
import os
import re
import socket
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
import time # For retry delays
import psutil # For system monitoring (CPU, RAM, Network)

# --- Custom Tooltip Class to avoid ttkbootstrap.tooltip TypeError on older Python versions ---
class CustomTooltip:
    """
    A custom tooltip class to provide basic tooltip functionality.
    This replaces ttkbootstrap.tooltip.Tooltip to avoid TypeError on Python < 3.10.
    """
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.id = None
        self.x = 0
        self.y = 0
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave) # Hide on click

    def enter(self, event=None):
        """Event handler for mouse entering the widget."""
        # Calculate position for the tooltip window
        self.x = self.widget.winfo_rootx() + 20 # Offset to the right of the widget
        self.y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5 # Offset below the widget
        self.show_tip()

    def leave(self, event=None):
        """Event handler for mouse leaving the widget."""
        self.hide_tip()

    def show_tip(self):
        """Creates and displays the tooltip window."""
        if self.tip_window or not self.text:
            return
        
        # Create a new top-level window for the tooltip
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True) # Removes window decorations (title bar, borders)
        self.tip_window.wm_geometry(f"+{self.x}+{self.y}") # Set its position

        # Create a label inside the tooltip window to display the text
        label = ttk.Label(self.tip_window, text=self.text, background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                          font=("tahoma", "8", "normal"), wraplength=250) # Added wraplength for longer tooltips
        label.pack(padx=1, pady=1)

    def hide_tip(self):
        """Destroys the tooltip window."""
        if self.tip_window:
            self.tip_window.destroy()
        self.tip_window = None

# --- Configuration File Management ---
CONFIG_FILE = "config.json"
CHANNELS_FILE = "channels.json"
DEFAULT_CONFIG = {
    "theme": "darkly",
    "logging_level": "INFO", # DEBUG, INFO, WARNING, ERROR, CRITICAL
    "ffmpeg_loglevel": "info", # FFmpeg's internal logging level
    "log_rotation_interval": "midnight", # 'midnight' or 'H' for hourly, 'D' for daily
    "log_retention_days": 7,
    "retry_attempts": 5, # Number of times to retry starting a stream if it fails
    "retry_delay_seconds": 10, # Delay between retry attempts
    "last_selected_channel": "Channel 1",
    "default_channels_count": 10,
    "udp_packet_timeout_seconds": 10, # Timeout for UDP packet reception check (for input status)
    "udp_check_interval_seconds": 60, # Interval for checking UDP packet flow (1 minute) - Kept for manual refresh
    "ffmpeg_process_monitor_interval_seconds": 5, # How often to check if FFmpeg process is still running
    "preview_auto_stop_seconds": 60, # New: Auto-stop preview after this many seconds
    "network_max_bandwidth_mbps": 100, # New: Max bandwidth for network utilization calculation (in Mbps)
    # "packet_loss_stop_retries": 3 # Removed: No longer automatically stopping on packet loss
}


def load_app_config():
    """Loads configuration from config.json, or creates it with defaults if not found."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            # Merge with defaults to ensure all keys are present
            for key, default_value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = default_value
            return config
        except json.JSONDecodeError:
            print(f"Error reading {CONFIG_FILE}. Creating with default settings.")
            with open(CONFIG_FILE, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            return DEFAULT_CONFIG
    else:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG

def save_app_config(config):
    """Saves the current application configuration to config.json."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving {CONFIG_FILE}: {e}") # Use print as logger might not be ready

def load_channels_config(default_count):
    """Loads channel configurations from channels.json, or initializes default channels."""
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, 'r') as f:
                channels = json.load(f)
            # Ensure all default channel properties are present in loaded channels
            for channel_name, channel_data in channels.items():
                if "display_name" not in channel_data:
                    channel_data["display_name"] = channel_name
                if "input_stream_status" not in channel_data:
                    channel_data["input_stream_status"] = "unknown"
                if "config" not in channel_data:
                    channel_data["config"] = {}
                # Ensure all default config keys are present within each channel's config
                for key, default_value in DEFAULT_CONFIG_CHANNEL_CONFIG.items():
                    if key not in channel_data["config"]:
                        channel_data["config"][key] = default_value
                if "programs" not in channel_data:
                    channel_data["programs"] = []
                if "last_known_streaming_state" not in channel_data: # New: for auto-start
                    channel_data["last_known_streaming_state"] = False
            return channels
        except json.JSONDecodeError:
            print(f"Error reading {CHANNELS_FILE}. Creating with default channels.")
            return _initialize_default_channels(default_count)
    else:
        return _initialize_default_channels(default_count)

def _initialize_default_channels(count):
    """Helper to create default channel structure."""
    channels = {}
    for i in range(1, count + 1):
        channel_name = f"Channel {i}"
        channels[channel_name] = {
            "display_name": channel_name,
            "input_stream_status": "unknown",
            "last_known_streaming_state": False, # New: for auto-start
            "config": {
                "input_type": "UDP", "input_ip": "239.1.1.1", "input_port": str(1233 + i), "input_url": "",
                "output_type": "UDP", "output_ip": "239.2.2.2", "output_port": str(5677 + i), "output_url": "",
                "video_bitrate": "2000", "program_id": "",
                "srt_mode": "listener", # Input SRT mode
                "local_bind_interface": "Auto",
                "output_srt_mode": "caller", # Output SRT mode
                "output_srt_latency": "5000", # Increased default: SRT output latency in ms
                "output_srt_maxbw": "0", # New: SRT output max bandwidth (0 for unlimited)
                "output_srt_tsbpdmode": "True", # New: SRT output TSBPD mode
                "output_srt_sndbuf": "8000000", # Increased default: SRT output send buffer size (0 for default)
                "output_srt_rcvbuf": "8000000", # Increased default: SRT output receive buffer size (0 for default)
                "output_udp_pkt_size": "1316", # New: UDP output packet size (for TS)
                "input_probesize": "10M", # New: Input probesize
                "input_analyzeduration": "10M", # New: Input analyzeduration
                "output_max_delay": "0" # New: Output max delay (0 for default)
            },
            "programs": []
        }
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(channels, f, indent=4)
    return channels

DEFAULT_CONFIG_CHANNEL_CONFIG = {
    "input_type": "UDP", "input_ip": "", "input_port": "", "input_url": "",
    "output_type": "UDP", "output_ip": "", "output_port": "", "output_url": "",
    "video_bitrate": "2000", "program_id": "",
    "srt_mode": "listener",
    "local_bind_interface": "Auto",
    "output_srt_mode": "caller",
    "output_srt_latency": "5000", # Increased default
    "output_srt_maxbw": "0",
    "output_srt_tsbpdmode": "True",
    "output_srt_sndbuf": "8000000", # Increased default
    "output_srt_rcvbuf": "8000000", # Increased default
    "output_udp_pkt_size": "1316",
    "input_probesize": "10M",
    "input_analyzeduration": "10M",
    "output_max_delay": "0"
}


def save_channels_config(channels):
    """Saves all channel configurations to channels.json."""
    try:
        with open(CHANNELS_FILE, 'w') as f:
            json.dump(channels, f, indent=4)
    except Exception as e:
        print(f"Error saving {CHANNELS_FILE}: {e}") # Use print as logger might not be ready

class FFmpegStreamerApp:
    def __init__(self, master):
        self.master = master
        self.app_config = load_app_config()
        self.master.title("VigilSiddhi Encoder") # Kept user's custom title
        self.master.geometry("1200x800")
        ttkb.Style(theme=self.app_config["theme"])

        # Set window icon
        try:
            icon_path = "logo.ico"
            if os.path.exists(icon_path):
                self.master.iconbitmap(icon_path)
            else:
                pass # Will log later if logger is ready
        except Exception as e:
            pass # Will log later if logger is ready

        # Setup logging
        self._setup_logging()
        
        # Now that logger is set up, log any deferred icon errors
        try:
            icon_path = "logo.ico"
            if not os.path.exists(icon_path):
                self.logger.warning(f"Icon file '{icon_path}' not found. Using default Python icon.")
        except Exception as e:
            self.logger.error(f"Error setting window icon: {e}. Using default Python icon.")


        self.channels = load_channels_config(self.app_config["default_channels_count"])
        self.processes = {} # Stores {channel_name: subprocess.Popen object}
        self.stderr_monitors = {} # Stores {channel_name: threading.Thread object for stderr monitoring}
        self.udp_listeners = {} # Stores {channel_name: socket object}
        self.udp_packet_timestamps = {} # Stores {channel_name: last_packet_received_time} (for UDP listener)
        self.udp_listener_threads = {} # Stores {channel_name: threading.Thread object for UDP listener}
        self.udp_listener_active_flags = {} # Stores {channel_name: threading.Event} to signal UDP listener thread to stop
        self.stream_stop_requested = {} # Stores {channel_name: boolean} to indicate if stop was user-initiated
        
        self.current_channel = None

        self.local_ip_addresses = self._get_local_ip_addresses()

        if not os.path.exists("logs"):
            os.makedirs("logs")

        # Preview process attributes (now for ffplay)
        self.ffplay_process = None
        self.ffplay_stderr_monitor = None # New: To monitor ffplay's stderr
        self.preview_running = False
        self.preview_auto_stop_id = None # To store after job ID for auto-stop
        self.current_preview_type = None # Stores "input" or "output" or None

        # Advanced options visibility state
        self.advanced_options_visible = tk.BooleanVar(value=False) # Initially hidden
        self.advanced_options_button_text = tk.StringVar(value="Advanced") # For the toggle button text

        # Initialize UDP listeners for all UDP channels on startup
        self.logger.info("Initializing UDP listeners for all configured UDP channels...")
        for channel_name, channel_data in self.channels.items():
            config = channel_data["config"]
            if config["input_type"] == "UDP":
                try:
                    udp_ip = config['input_ip']
                    udp_port = int(config['input_port'])
                    bind_address = config['local_bind_interface']
                    if bind_address == "Auto":
                        bind_address = "0.0.0.0"
                    self._start_udp_listener(channel_name, udp_ip, udp_port, bind_address)
                except ValueError:
                    self.logger.error(f"[{channel_name}] Invalid UDP port '{config['input_port']}' for listener on startup. Setting status to unavailable.")
                    self._set_input_stream_status(channel_name, "unavailable")
                except Exception as e:
                    self.logger.error(f"[{channel_name}] Error starting UDP listener on startup: {e}. Setting status to unavailable.")
                    self._set_input_stream_status(channel_name, "unavailable")
            else:
                # For non-UDP, set initial status to unknown (grey) or available if URL is present
                if self.get_input_url(config):
                    self._set_input_stream_status(channel_name, "available")
                else:
                    self._set_input_stream_status(channel_name, "unknown")


        # Auto-start streams that were running at last shutdown
        # This needs to be done *after* initial status setting, but before mainloop
        self.logger.info("Checking for streams to auto-start from last session...")
        for channel_name, channel_data in list(self.channels.items()):
            if channel_data.get("last_known_streaming_state", False):
                self.logger.info(f"Attempting to auto-start stream for '{channel_name}' based on last known state.")
                # Use master.after to schedule the start, so it doesn't block UI during startup
                self.master.after(100, self.start_stream_internal, channel_name)

        # Start the periodic FFmpeg process monitor thread (only checks if process is running)
        self.process_monitor_thread = threading.Thread(target=self._monitor_ffmpeg_processes)
        self.process_monitor_thread.daemon = True
        self.process_monitor_thread.start()

        # Main frame for the entire application layout
        main_frame = ttk.Frame(self.master, padding="15") # Increased padding
        main_frame.pack(fill=BOTH, expand=True)

        # Left frame for channel list and selection
        left_frame = ttk.Frame(main_frame, width=250, style='TFrame') # Added style for consistent background
        left_frame.pack(side=LEFT, fill=Y, padx=(0, 15)) # Increased padx
        
        ttk.Label(left_frame, text="Channels", font=("Helvetica", 16, "bold"), bootstyle="primary").pack(pady=15) # Larger font, primary color

        self.channel_list_frame = ttk.Frame(left_frame)
        self.channel_list_frame.pack(fill=BOTH, expand=True, pady=(0, 10)) # Added pady
        self.channel_buttons = {}
        
        self._populate_channel_list()

        # Right frame for configuration and meters, with a scrollbar
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        # Create a canvas and scrollbar for the right frame
        self.canvas = tk.Canvas(right_frame, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        # Bind the canvas's Configure event to resize the scrollable_frame's width
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        self.canvas_window_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.scrollbar.pack(side=RIGHT, fill=Y)

        # Content for the scrollable frame
        top_bar_frame = ttk.Frame(self.scrollable_frame, style='TFrame') # Added style
        top_bar_frame.pack(fill=X, pady=(0, 15)) # Increased pady

        status_frame = ttk.Frame(top_bar_frame)
        status_frame.pack(side=LEFT, anchor='w', padx=(0, 20)) # Added padx
        
        ttk.Label(status_frame, text="Stream Status:", font=("Helvetica", 16, "bold"), bootstyle="primary").pack(side=LEFT, padx=(0,10)) # Increased font size
        
        self.status_indicators_frame = ttk.Frame(status_frame)
        self.status_indicators_frame.pack(side=LEFT)
        self.status_indicators = {}
        self.status_tooltips = {} # Stores {channel_name: Tooltip object}
        self.create_status_indicators()

        # --- System Resource Progress Bars ---
        self.system_meters_frame = ttk.Frame(top_bar_frame, style='TFrame')
        self.system_meters_frame.pack(side=LEFT, padx=10, anchor='w')

        # Initialize psutil for network bytes
        psutil.net_io_counters.cache_clear()
        self.last_net_bytes_sent = psutil.net_io_counters().bytes_sent
        self.last_net_bytes_recv = psutil.net_io_counters().bytes_recv
        self.last_net_time = time.time()

        # CPU Progress Bar
        cpu_pb_frame = ttk.Frame(self.system_meters_frame)
        cpu_pb_frame.pack(side=LEFT, padx=5, pady=2)
        self.cpu_pb = ttk.Progressbar(cpu_pb_frame, orient="horizontal", length=75, mode="determinate")
        self.cpu_pb.pack(side=TOP)
        self.cpu_pb_label = ttk.Label(cpu_pb_frame, text="CPU", font=("Helvetica", 9, "bold"))
        self.cpu_pb_label.pack(side=BOTTOM)

        # RAM Progress Bar
        ram_pb_frame = ttk.Frame(self.system_meters_frame)
        ram_pb_frame.pack(side=LEFT, padx=5, pady=2)
        self.ram_pb = ttk.Progressbar(ram_pb_frame, orient="horizontal", length=75, mode="determinate")
        self.ram_pb.pack(side=TOP)
        self.ram_pb_label = ttk.Label(ram_pb_frame, text="RAM", font=("Helvetica", 9, "bold"))
        self.ram_pb_label.pack(side=BOTTOM)

        # Network Progress Bar
        network_pb_frame = ttk.Frame(self.system_meters_frame)
        network_pb_frame.pack(side=LEFT, padx=5, pady=2)
        self.network_pb = ttk.Progressbar(network_pb_frame, orient="horizontal", length=75, mode="determinate")
        self.network_pb.pack(side=TOP)
        self.network_pb_label = ttk.Label(network_pb_frame, text="NW", font=("Helvetica", 9, "bold"))
        self.network_pb_label.pack(side=BOTTOM)


        self.logo_label = ttk.Label(top_bar_frame)
        self.logo_label.pack(side=RIGHT, anchor='ne', padx=10) # Re-pack to the far right
        self.load_logo() # Load logo after its label is packed

        # --- Tabbed Interface for Configuration (only one tab now) ---
        self.notebook = ttk.Notebook(self.scrollable_frame)
        self.notebook.pack(fill=BOTH, expand=True, pady=(15,0)) # Increased pady

        # Configuration Tab
        self.config_tab = ttk.Frame(self.notebook, padding="20") # Increased padding
        self.notebook.add(self.config_tab, text="Configuration")
        
        # Main configuration frame within the tab
        self.config_frame = ttk.LabelFrame(self.config_tab, text="Configuration", padding="20", bootstyle="primary") # Increased padding, added bootstyle
        self.config_frame.pack(fill=BOTH, expand=True)
        
        self.create_config_widgets()
        
        # Select the last active channel on startup
        initial_channel = self.app_config.get("last_selected_channel", "Channel 1")
        if initial_channel in self.channels:
            self.select_channel(initial_channel)
        else:
            # Fallback if last_selected_channel is invalid
            first_channel = next(iter(self.channels), None)
            if first_channel:
                self.select_channel(first_channel)
            else:
                self.logger.warning("No channels found to select on startup.")
                self.update_ui_for_channel() # Update UI even if no channel is selected

        # Schedule periodic UI updates (for system metrics and indicator colors)
        self._schedule_ui_update()
        self._update_system_metrics() # Start system metrics update loop


    def _schedule_ui_update(self):
        """Schedules a periodic update of the UI status indicators."""
        self._refresh_all_stream_statuses() # Call refresh here
        # The update_status_indicators and update_ui_for_channel calls are now inside _set_input_stream_status
        # which is called by _refresh_all_stream_statuses if a change occurs.
        # This prevents redundant calls and ensures consistency.
        self.master.after(1000, self._schedule_ui_update) # Update every 1 second

    def _setup_logging(self):
        """Sets up the application's logging system with timed rotating files."""
        self.logger = logging.getLogger("FFmpegStreamerApp")
        # Set the logger's level based on config
        self.logger.setLevel(self.app_config["logging_level"])

        # Prevent duplicate handlers if called multiple times
        if not self.logger.handlers:
            # Create logs directory if it doesn't exist
            if not os.path.exists("logs"):
                os.makedirs("logs")

            # Console handler for immediate feedback
            console_handler = logging.StreamHandler()
            # Set console handler level to INFO or DEBUG based on config
            # This ensures debug messages only appear if explicitly enabled in config
            console_handler.setLevel(self.app_config["logging_level"]) 
            console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

            # File handler for daily log rotation
            file_handler = TimedRotatingFileHandler(
                os.path.join("logs", "app.log"),
                when=self.app_config["log_rotation_interval"],
                interval=1,
                backupCount=self.app_config["log_retention_days"]
            )
            # File handler should always log at the main logger's level or higher
            file_handler.setLevel(self.app_config["logging_level"]) # Ensure file logs at configured level
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        self.logger.info("Application logging initialized.")

    def _populate_channel_list(self):
        """Populates the channel list buttons based on loaded channels."""
        for channel_name in self.channels.keys():
            # Use 'outline-secondary' for a modern, less intrusive default look
            btn = ttk.Button(self.channel_list_frame, text=self.channels[channel_name]["display_name"],
                             command=lambda name=channel_name: self.select_channel(name), style='outline-secondary.TButton', padding=10)
            btn.pack(fill=X, pady=4) # Increased pady
            self.channel_buttons[channel_name] = btn

    def _get_local_ip_addresses(self):
        """
        Discovers and returns a list of local IP addresses available on the machine.
        Includes 'Auto' (for 0.0.0.0 binding) and '127.0.0.1' (localhost).
        """
        ips = ["Auto"]
        try:
            hostname = socket.gethostname()
            ip_list = socket.gethostbyname_ex(hostname)[2]
            for ip in ip_list:
                if ip not in ips:
                    ips.append(ip)
        except socket.gaierror:
            self.logger.warning("Could not resolve local hostname to IP addresses.")
        except Exception as e:
            self.logger.error(f"Error getting local IP addresses: {e}")

        if "127.0.0.1" not in ips:
            ips.append("127.0.0.1")

        return sorted(ips)

    def create_status_indicators(self):
        """
        Creates or re-creates the small colored canvas indicators in the top bar.
        This is called when channels are added or renamed to update the display.
        """
        for widget in self.status_indicators_frame.winfo_children():
            widget.destroy()
        self.status_indicators.clear()
        self.status_tooltips.clear()
        
        for channel_name in self.channels.keys():
            canvas = tk.Canvas(self.status_indicators_frame, width=20, height=20, bg='grey', highlightthickness=0, relief=tk.RIDGE, bd=1) # Added relief and bd
            canvas.pack(side=LEFT, padx=5) # Increased padx
            self.status_indicators[channel_name] = canvas
            # Create a tooltip for each indicator
            self.status_tooltips[channel_name] = CustomTooltip(canvas, text="") # Changed to CustomTooltip

    def load_logo(self):
        """Loads 'logo.png' and displays it in the top right corner with user-specified size."""
        try:
            logo_path = "logo.png" # Kept user's custom logo path
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                img.thumbnail((175, 75), Image.Resampling.LANCZOS) # Kept user's custom logo size
                self.logo_photo = ImageTk.PhotoImage(img)
                self.logo_label.config(image=self.logo_photo)
        except Exception as e:
            self.logger.error(f"Error loading logo: {e}")

    def create_config_widgets(self):
        """Initializes all input fields, comboboxes, and buttons in the configuration panel."""
        self.input_group = ttk.LabelFrame(self.config_frame, text="Input Configuration", padding="15", bootstyle="info") # Changed to self.input_group, increased padding, added bootstyle
        self.input_group.pack(fill=X, pady=10) # Increased pady
        self.input_group.columnconfigure(1, weight=1)

        ttk.Label(self.input_group, text="Channel Display Name:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.display_name_var = tk.StringVar()
        self.display_name_entry = ttk.Entry(self.input_group, textvariable=self.display_name_var)
        self.display_name_entry.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky='we')
        self.display_name_var.trace_add("write", self._on_display_name_change)

        ttk.Label(self.input_group, text="Input Type:").grid(row=1, column=0, padx=5, pady=5, sticky=W)
        self.input_type_var = tk.StringVar(value="UDP")
        self.input_type_combo = ttk.Combobox(self.input_group, textvariable=self.input_type_var, values=["UDP", "SRT", "HLS (M3U8)", "YouTube"], state='readonly', width=15)
        self.input_type_combo.grid(row=1, column=1, padx=5, pady=5, sticky=W)
        self.input_type_combo.bind("<<ComboboxSelected>>", self.on_input_type_change)

        # These frames will be gridded dynamically by on_input_type_change
        self.input_ip_port_frame = ttk.Frame(self.input_group)
        self.input_ip_port_frame.columnconfigure(1, weight=1)
        ttk.Label(self.input_ip_port_frame, text="IP Address:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.input_ip_var = tk.StringVar()
        ttk.Entry(self.input_ip_port_frame, textvariable=self.input_ip_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        ttk.Label(self.input_ip_port_frame, text="Port:").grid(row=0, column=2, padx=5, pady=2, sticky=W)
        self.input_port_var = tk.StringVar()
        ttk.Entry(self.input_ip_port_frame, textvariable=self.input_port_var, width=10).grid(row=0, column=3, padx=5, pady=2, sticky=W)
        
        self.input_url_frame = ttk.Frame(self.input_group)
        self.input_url_frame.columnconfigure(1, weight=1)
        ttk.Label(self.input_url_frame, text="URL:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.input_url_var = tk.StringVar()
        ttk.Entry(self.input_url_frame, textvariable=self.input_url_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')

        self.srt_mode_frame = ttk.Frame(self.input_group)
        self.srt_mode_frame.columnconfigure(1, weight=1)
        ttk.Label(self.srt_mode_frame, text="SRT Mode:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.srt_mode_var = tk.StringVar(value="listener")
        self.srt_mode_combo = ttk.Combobox(self.srt_mode_frame, textvariable=self.srt_mode_var, values=["listener", "caller"], state='readonly', width=15)
        self.srt_mode_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)

        self.local_bind_interface_frame = ttk.Frame(self.input_group)
        self.local_bind_interface_frame.columnconfigure(1, weight=1)
        ttk.Label(self.local_bind_interface_frame, text="Local Bind Interface:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.local_bind_interface_var = tk.StringVar(value="Auto")
        self.local_bind_interface_combo = ttk.Combobox(self.local_bind_interface_frame, textvariable=self.local_bind_interface_var, values=self.local_ip_addresses, state='readonly', width=20)
        self.local_bind_interface_combo.grid(row=0, column=1, padx=5, pady=2, sticky='we')

        self.scan_button = ttk.Button(self.input_group, text="Scan for Services", command=self.scan_services, style='info.TButton') # Added style
        
        ttk.Label(self.input_group, text="Select Service/Program:").grid(row=3, column=0, padx=5, pady=5, sticky=W)
        self.program_id_var = tk.StringVar()
        self.program_id_combo = ttk.Combobox(self.input_group, textvariable=self.program_id_var, state='readonly')
        self.program_id_combo.grid(row=3, column=1, padx=5, pady=5, sticky='we')
        self.program_id_combo.set("Scan input to populate")

        # --- Output Configuration ---
        output_group = ttk.LabelFrame(self.config_frame, text="Output Configuration", padding="15", bootstyle="info") # Increased padding, added bootstyle
        output_group.pack(fill=X, pady=10) # Increased pady
        output_group.columnconfigure(1, weight=1) 
        output_group.columnconfigure(2, weight=0)
        output_group.columnconfigure(3, weight=0)

        ttk.Label(output_group, text="Output Type:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.output_type_var = tk.StringVar(value="UDP")
        self.output_type_combo = ttk.Combobox(output_group, textvariable=self.output_type_var, values=["UDP", "SRT", "RTMP", "RTP"], state='readonly', width=15)
        self.output_type_combo.grid(row=0, column=1, padx=5, pady=5, sticky=W)
        self.output_type_combo.bind("<<ComboboxSelected>>", self.on_output_type_change)

        self.output_ip_port_frame = ttk.Frame(output_group)
        self.output_ip_port_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_ip_port_frame, text="IP Address:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_ip_var = tk.StringVar()
        ttk.Entry(self.output_ip_port_frame, textvariable=self.output_ip_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        ttk.Label(self.output_ip_port_frame, text="Port:").grid(row=0, column=2, padx=5, pady=2, sticky=W)
        self.output_port_var = tk.StringVar()
        ttk.Entry(self.output_ip_port_frame, textvariable=self.output_port_var, width=10).grid(row=0, column=3, padx=5, pady=2, sticky=W)

        self.output_url_frame = ttk.Frame(output_group)
        self.output_url_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_url_frame, text="URL:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_url_var = tk.StringVar()
        ttk.Entry(self.output_url_frame, textvariable=self.output_url_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')

        self.output_srt_mode_frame = ttk.Frame(output_group)
        self.output_srt_mode_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_srt_mode_frame, text="SRT Mode:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_mode_var = tk.StringVar(value="caller")
        self.output_srt_mode_combo = ttk.Combobox(self.output_srt_mode_frame, textvariable=self.output_srt_mode_var, values=["caller", "listener"], state='readonly', width=15)
        self.output_srt_mode_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)

        self.output_rtp_protocol_frame = ttk.Frame(output_group)
        self.output_rtp_protocol_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_rtp_protocol_frame, text="RTP Protocol:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_rtp_protocol_var = tk.StringVar(value="udp")
        self.output_rtp_protocol_combo = ttk.Combobox(self.output_rtp_protocol_frame, textvariable=self.output_rtp_protocol_var, values=["udp", "tcp"], state='readonly', width=15)
        self.output_rtp_protocol_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)

        self.output_rtp_payload_type_frame = ttk.Frame(output_group)
        self.output_rtp_payload_type_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_rtp_payload_type_frame, text="RTP Payload Type:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_rtp_payload_type_var = tk.StringVar(value="96")
        ttk.Entry(self.output_rtp_payload_type_frame, textvariable=self.output_rtp_payload_type_var, width=10).grid(row=0, column=1, padx=5, pady=2, sticky=W)

        self.video_bitrate_label = ttk.Label(output_group, text="Video Bitrate (kbps):")
        self.video_bitrate_var = tk.StringVar(value="2000")
        self.video_bitrate_entry = ttk.Entry(output_group, textvariable=self.video_bitrate_var, width=15)
        
        self.toggle_advanced_options_button = ttk.Button(output_group, 
                                                         textvariable=self.advanced_options_button_text, 
                                                         command=self.toggle_advanced_options, 
                                                         style='secondary.TButton')

        # --- Advanced Stream Options Group ---
        self.advanced_options_group = ttk.LabelFrame(self.config_frame, text="Advanced Stream Options", padding="15", bootstyle="info") # Increased padding, added bootstyle
        self.advanced_options_group.columnconfigure(1, weight=1)
        self.advanced_options_group.pack_forget() # Ensure it's hidden and takes no space initially
        
        ttk.Label(self.advanced_options_group, text="Input Probesize:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.input_probesize_var = tk.StringVar()
        ttk.Entry(self.advanced_options_group, textvariable=self.input_probesize_var, width=15).grid(row=0, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.advanced_options_group, text="(e.g., 10M)").grid(row=0, column=2, padx=2, pady=2, sticky=W)

        ttk.Label(self.advanced_options_group, text="Input Analyzeduration:").grid(row=1, column=0, padx=5, pady=2, sticky=W)
        self.input_analyzeduration_var = tk.StringVar()
        ttk.Entry(self.advanced_options_group, textvariable=self.input_analyzeduration_var, width=15).grid(row=1, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.advanced_options_group, text="(e.g., 10M)").grid(row=1, column=2, padx=2, pady=2, sticky=W)

        ttk.Label(self.advanced_options_group, text="Output Max Delay (us):").grid(row=2, column=0, padx=5, pady=2, sticky=W)
        self.output_max_delay_var = tk.StringVar()
        ttk.Entry(self.advanced_options_group, textvariable=self.output_max_delay_var, width=15).grid(row=2, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.advanced_options_group, text="(0 for default)").grid(row=2, column=2, padx=2, pady=2, sticky=W)

        self.srt_output_options_frame = ttk.Frame(self.advanced_options_group)
        self.srt_output_options_frame.columnconfigure(1, weight=1)
        
        ttk.Label(self.srt_output_options_frame, text="SRT Latency (ms):").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_latency_var = tk.StringVar()
        ttk.Entry(self.srt_output_options_frame, textvariable=self.output_srt_latency_var, width=10).grid(row=0, column=1, padx=5, pady=2, sticky=W)

        ttk.Label(self.srt_output_options_frame, text="SRT Max Bandwidth (pkts/s):").grid(row=1, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_maxbw_var = tk.StringVar()
        ttk.Entry(self.srt_output_options_frame, textvariable=self.output_srt_maxbw_var, width=10).grid(row=1, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.srt_output_options_frame, text="(0 for unlimited)").grid(row=1, column=2, padx=2, pady=2, sticky=W)

        ttk.Label(self.srt_output_options_frame, text="SRT TSBPD Mode:").grid(row=2, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_tsbpdmode_var = tk.StringVar(value="True")
        ttk.Combobox(self.srt_output_options_frame, textvariable=self.output_srt_tsbpdmode_var, values=["True", "False"], state='readonly', width=10).grid(row=2, column=1, padx=5, pady=2, sticky=W)

        ttk.Label(self.srt_output_options_frame, text="SRT Send Buffer (bytes):").grid(row=3, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_sndbuf_var = tk.StringVar()
        ttk.Entry(self.srt_output_options_frame, textvariable=self.output_srt_sndbuf_var, width=10).grid(row=3, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.srt_output_options_frame, text="(0 for default)").grid(row=3, column=2, padx=2, pady=2, sticky=W)

        ttk.Label(self.srt_output_options_frame, text="SRT Receive Buffer (bytes):").grid(row=4, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_rcvbuf_var = tk.StringVar()
        ttk.Entry(self.srt_output_options_frame, textvariable=self.output_srt_rcvbuf_var, width=10).grid(row=4, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.srt_output_options_frame, text="(0 for default)").grid(row=4, column=2, padx=2, pady=2, sticky=W)

        self.udp_output_options_frame = ttk.Frame(self.advanced_options_group)
        self.udp_output_options_frame.columnconfigure(1, weight=1)

        ttk.Label(self.udp_output_options_frame, text="UDP Packet Size:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_udp_pkt_size_var = tk.StringVar()
        ttk.Entry(self.udp_output_options_frame, textvariable=self.output_udp_pkt_size_var, width=10).grid(row=0, column=1, padx=5, pady=2, sticky=W)
        ttk.Label(self.udp_output_options_frame, text="(e.g., 1316 for TS)").grid(row=0, column=2, padx=2, pady=2, sticky=W)
        
        # --- Combined Action Buttons Frame ---
        self.action_buttons_frame = ttk.Frame(self.config_tab, padding="15") # Increased padding
        self.action_buttons_frame.pack(side=BOTTOM, fill=X, pady=10)
        self.action_buttons_frame.columnconfigure(0, weight=1) # Left spacer
        self.action_buttons_frame.columnconfigure(1, weight=0) # Save
        self.action_buttons_frame.columnconfigure(2, weight=0) # Refresh (now global)
        self.action_buttons_frame.columnconfigure(3, weight=1) # Middle spacer
        self.action_buttons_frame.columnconfigure(4, weight=0) # Play/Stop Preview (single button)
        self.action_buttons_frame.columnconfigure(5, weight=0) # Start Stream
        self.action_buttons_frame.columnconfigure(6, weight=0) # Stop Stream
        self.action_buttons_frame.columnconfigure(7, weight=1) # Right spacer

        self.save_button = ttk.Button(self.action_buttons_frame, text="Save Configuration", command=self.save_and_validate_config, style='info.TButton')
        self.save_button.grid(row=0, column=1, padx=5, pady=5)

        # Global Refresh Status Button
        self.global_refresh_button = ttk.Button(self.action_buttons_frame, text="Refresh All Statuses", command=self._refresh_all_stream_statuses, style='secondary.TButton')
        self.global_refresh_button.grid(row=0, column=2, padx=5, pady=5)

        # Separate buttons for input and output preview
        self.preview_input_button = ttk.Button(self.action_buttons_frame, text="Preview Input", command=lambda: self.toggle_preview("input"), style='primary.TButton')
        self.preview_input_button.grid(row=0, column=4, padx=5, pady=5)

        self.preview_output_button = ttk.Button(self.action_buttons_frame, text="Preview Output", command=lambda: self.toggle_preview("output"), style='secondary.TButton')
        self.preview_output_button.grid(row=0, column=5, padx=5, pady=5)

        self.start_button = ttk.Button(self.action_buttons_frame, text="Start Stream", command=self.start_stream, style='success.TButton') # Changed to success
        self.start_button.grid(row=0, column=6, padx=5, pady=5)
        self.stop_button = ttk.Button(self.action_buttons_frame, text="Stop Stream", command=self.stop_stream, style='danger.TButton') # Changed to danger
        self.stop_button.grid(row=0, column=7, padx=5, pady=5)
        
        self.on_input_type_change()
        self.on_output_type_change()

    def toggle_advanced_options(self):
        """Toggles the visibility of the advanced options group and adjusts button text."""
        if self.advanced_options_visible.get():
            self.advanced_options_group.pack_forget() # Use pack_forget to remove space
            self.advanced_options_visible.set(False)
            self.advanced_options_button_text.set("Advanced")
        else:
            self.advanced_options_group.pack(fill=X, pady=10) # Use pack to occupy space, increased pady
            self.advanced_options_visible.set(True)
            self.on_output_type_change() # This will grid the specific SRT/UDP frames within advanced_options_group
            self.advanced_options_button_text.set("Hide Advanced") # Set after on_output_type_change
        
        # Update scroll region after toggling visibility
        self.master.after_idle(self._update_scroll_region)

    def _on_canvas_configure(self, event):
        """Resizes the scrollable frame to match the canvas width and updates scroll region."""
        # Get the current width of the canvas
        canvas_width = event.width
        # Set the width of the scrollable frame to the canvas width
        # This is crucial for horizontal expansion
        self.canvas.itemconfig(self.canvas_window_id, width=canvas_width)
        # Update the scroll region to reflect the new size of the scrollable frame
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))


    def _update_scroll_region(self):
        """Updates the scroll region of the canvas to fit its content."""
        self.canvas.update_idletasks() # Ensure all widgets are rendered
        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def manual_refresh_status(self):
        """Manually triggers an update of all channel status indicators."""
        self.logger.info("Manual status refresh triggered.")
        self._refresh_all_stream_statuses() # Call refresh here
        # The update_status_indicators and update_ui_for_channel calls are now inside _set_input_stream_status
        # which is called by _refresh_all_stream_statuses if a change occurs.
        # This prevents redundant calls and ensures consistency.

    def _on_display_name_change(self, *args):
        """Updates the channel button text and internal data when the display name entry changes."""
        if self.current_channel:
            new_display_name = self.display_name_var.get()
            if self.current_channel in self.channel_buttons:
                self.channel_buttons[self.current_channel].config(text=new_display_name)
            self.channels[self.current_channel]["display_name"] = new_display_name
            self.update_status_indicators() # This will update the button text and color

    def on_input_type_change(self, event=None):
        """Adjusts visibility of input fields (IP/Port vs. URL) and new SRT/Interface options based on selected input type."""
        input_type = self.input_type_var.get()
        
        # Hide all dynamic input-specific frames and buttons first
        self.input_url_frame.grid_forget()
        self.input_ip_port_frame.grid_forget()
        self.srt_mode_frame.grid_forget()
        self.local_bind_interface_frame.grid_forget()
        self.scan_button.grid_forget() # Hide scan button initially

        # Always ensure program_id_combo is in the correct state
        if input_type == "UDP":
            self.program_id_combo.config(state='readonly')
            if self.program_id_var.get() == "N/A for SRT" or self.program_id_var.get() == "N/A for this input type":
                self.program_id_var.set("Scan input to populate")
        else:
            self.program_id_combo.config(state='disabled')
            self.program_id_var.set("N/A for this input type")

        # Place input type specific widgets
        if input_type in ["UDP", "SRT"]:
            self.input_ip_port_frame.grid(row=2, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Row 2 for IP/Port
            self.local_bind_interface_frame.grid(row=4, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Row 4 for Local Bind
            
            if input_type == "SRT":
                self.srt_mode_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Row 5 for SRT Mode
                self.scan_button.config(state='disabled')
            else: # UDP
                self.scan_button.config(state='normal')
                self.scan_button.grid(row=3, column=2, padx=5, pady=5, sticky=E) # Scan button at Row 3, Col 2
        else: # HLS, YouTube
            self.input_url_frame.grid(row=2, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Row 2 for URL
        
        self.master.after_idle(self._update_scroll_region)


    def on_output_type_change(self, event=None):
        """Adjusts visibility of output fields based on selected output type."""
        output_type = self.output_type_var.get()

        # Hide all output-specific frames first
        self.output_ip_port_frame.grid_forget()
        self.output_url_frame.grid_forget()
        self.output_srt_mode_frame.grid_forget()
        self.output_rtp_protocol_frame.grid_forget()
        self.output_rtp_payload_type_frame.grid_forget()
        
        # Hide advanced output options frames within the advanced_options_group
        # These are only visible if advanced_options_group itself is visible
        self.srt_output_options_frame.grid_forget() 
        self.udp_output_options_frame.grid_forget() 


        # Determine the current row for dynamically placed elements within output_group
        current_row_for_dynamic_elements = 1 # Starts after "Output Type" (row 0)

        # Show relevant frames based on selection and update current_row_for_dynamic_elements
        if output_type == "UDP":
            self.output_ip_port_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=4, padx=5, pady=2, sticky='we') # Use columnspan=4 for consistency
            current_row_for_dynamic_elements += 1
            self.video_bitrate_entry.config(state='normal')
            if self.advanced_options_visible.get(): # Only show if advanced options are globally visible
                self.udp_output_options_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Grid within advanced_options_group
        elif output_type == "SRT":
            self.output_ip_port_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=4, padx=5, pady=2, sticky='we')
            current_row_for_dynamic_elements += 1
            self.output_srt_mode_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=2, padx=5, pady=2, sticky='we')
            current_row_for_dynamic_elements += 1
            self.video_bitrate_entry.config(state='normal')
            if self.advanced_options_visible.get(): # Only show if advanced options are globally visible
                self.srt_output_options_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=2, sticky='we') # Grid within advanced_options_group
        elif output_type == "RTMP":
            self.output_url_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=2, padx=5, pady=2, sticky='we')
            current_row_for_dynamic_elements += 1
            self.video_bitrate_entry.config(state='normal')
        elif output_type == "RTP":
            self.output_ip_port_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=4, padx=5, pady=2, sticky='we')
            current_row_for_dynamic_elements += 1
            self.output_rtp_protocol_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=2, padx=5, pady=2, sticky=W)
            current_row_for_dynamic_elements += 1
            self.output_rtp_payload_type_frame.grid(row=current_row_for_dynamic_elements, column=0, columnspan=2, padx=5, pady=2, sticky=W)
            current_row_for_dynamic_elements += 1
            self.video_bitrate_entry.config(state='normal')

        # Place the Video Bitrate Label and Entry
        self.video_bitrate_label.grid(row=current_row_for_dynamic_elements, column=0, padx=5, pady=5, sticky=W)
        self.video_bitrate_entry.grid(row=current_row_for_dynamic_elements, column=1, padx=5, pady=5, sticky=W)
        current_row_for_dynamic_elements += 1

        # Place the Advanced Options Toggle Button at the bottom right of the output_group
        self.toggle_advanced_options_button.grid(row=current_row_for_dynamic_elements, column=1, padx=5, pady=5, sticky=SE)
        self.toggle_advanced_options_button.master.columnconfigure(1, weight=1)
        self.toggle_advanced_options_button.master.grid_rowconfigure(current_row_for_dynamic_elements, weight=1)
        
        self.master.after_idle(self._update_scroll_region)


    def select_channel(self, channel_name):
        """
        Handles channel selection from the left sidebar.
        Saves current config, loads new channel's config, and updates UI.
        """
        self.logger.debug(f"Selecting channel: {channel_name}")
        if self.current_channel:
            self.save_current_config_to_memory()
            # No longer stopping UDP listener for previous channel here.
            # UDP listeners run persistently for all configured UDP inputs.
            self.logger.debug(f"Saved config for previous channel: {self.current_channel}")
            # Stop preview if it was running for the old channel
            self._stop_preview_internal()
            self.logger.debug("Stopped any active preview.")

        self.current_channel = channel_name
        self.app_config["last_selected_channel"] = channel_name # Update last selected channel
        save_app_config(self.app_config) # Save config immediately
        self.logger.info(f"Current channel set to '{channel_name}'. App config saved.")

        self.config_frame.config(text=f"Configuration for {self.channels[channel_name]['display_name']}")
        self.load_channel_config()
        self.logger.debug(f"Loaded config for current channel: {channel_name}")
        
        # Trigger a refresh to ensure all statuses are up-to-date after channel switch.
        # This is where the status of the newly selected channel will be determined
        # based on its UDP listener state (if UDP) or URL presence (if non-UDP).
        self.master.after(0, self._refresh_all_stream_statuses)
        self.master.after(0, self.update_ui_for_channel) # Update UI after loading new channel config

    def save_current_config_to_memory(self):
        """Saves the current UI input values into the selected channel's configuration dictionary."""
        if not self.current_channel: return
        config = self.channels[self.current_channel]["config"]
        self.channels[self.current_channel]["display_name"] = self.display_name_var.get()
        config["input_type"] = self.input_type_var.get()
        config["input_ip"] = self.input_ip_var.get()
        config["input_port"] = self.input_port_var.get()
        config["input_url"] = self.input_url_var.get()
        config["output_type"] = self.output_type_var.get()
        config["output_ip"] = self.output_ip_var.get()
        config["output_port"] = self.output_port_var.get()
        config["output_url"] = self.output_url_var.get() # For RTMP
        config["video_bitrate"] = self.video_bitrate_var.get()
        config["srt_mode"] = self.srt_mode_var.get() # Input SRT mode
        config["local_bind_interface"] = self.local_bind_interface_var.get()
        config["output_srt_mode"] = self.output_srt_mode_var.get() # Output SRT mode
        config["output_srt_latency"] = self.output_srt_latency_var.get()
        config["output_srt_maxbw"] = self.output_srt_maxbw_var.get()
        config["output_srt_tsbpdmode"] = self.output_srt_tsbpdmode_var.get()
        config["output_srt_sndbuf"] = self.output_srt_sndbuf_var.get()
        config["output_srt_rcvbuf"] = self.output_srt_rcvbuf_var.get()
        config["output_udp_pkt_size"] = self.output_udp_pkt_size_var.get()
        config["input_probesize"] = self.input_probesize_var.get()
        config["input_analyzeduration"] = self.input_analyzeduration_var.get()
        config["output_max_delay"] = self.output_max_delay_var.get()

        # Update last_known_streaming_state based on current process status
        self.channels[self.current_channel]["last_known_streaming_state"] = self.current_channel in self.processes


        # Save the selected program ID
        if self.input_type_var.get() == "UDP" and self.program_id_var.get():
            selected_text = self.program_id_var.get()
            match = re.search(r'\(ID: (\d+)\)', selected_text)
            if match:
                config["program_id"] = match.group(1)
            else:
                config["program_id"] = "" # Clear if no valid ID found
        else:
            config["program_id"] = "" # Clear for non-UDP or no selection

    def load_channel_config(self):
        """Loads the selected channel's configuration from memory into the UI input fields."""
        if not self.current_channel: return
        channel_data = self.channels[self.current_channel]
        config = channel_data["config"]

        self.display_name_var.set(channel_data.get("display_name", self.current_channel))
        self.input_type_var.set(config.get("input_type", "UDP"))
        self.input_ip_var.set(config.get("input_ip", ""))
        self.input_port_var.set(config.get("input_port", ""))
        self.input_url_var.set(config.get("input_url", ""))
        self.output_type_var.set(config.get("output_type", "UDP"))
        self.output_ip_var.set(config.get("output_ip", ""))
        self.output_port_var.set(config.get("output_port", ""))
        self.output_url_var.set(config.get("output_url", "")) # For RTMP
        self.video_bitrate_var.set(config.get("video_bitrate", "2000"))
        self.srt_mode_var.set(config.get("srt_mode", "listener"))
        self.local_bind_interface_var.set(config.get("local_bind_interface", "Auto"))
        self.output_srt_mode_var.set(config.get("output_srt_mode", "caller")) # Output SRT mode
        self.output_srt_latency_var.set(config.get("output_srt_latency", "5000"))
        self.output_srt_maxbw_var.set(config.get("output_srt_maxbw", "0"))
        self.output_srt_tsbpdmode_var.set(config.get("output_srt_tsbpdmode", "True"))
        self.output_srt_sndbuf_var.set(config.get("output_srt_sndbuf", "8000000"))
        self.output_srt_rcvbuf_var.set(config.get("output_srt_rcvbuf", "8000000"))
        self.output_udp_pkt_size_var.set(config.get("output_udp_pkt_size", "1316"))
        self.input_probesize_var.set(config.get("input_probesize", "10M"))
        self.input_analyzeduration_var.set(config.get("input_analyzeduration", "10M"))
        self.output_max_delay_var.set(config.get("output_max_delay", "0"))
        
        self.on_input_type_change()
        self.on_output_type_change() # Call this to set visibility based on loaded type

        programs = channel_data.get("programs", [])
        if self.input_type_var.get() == "UDP":
            if programs:
                display_list = []
                has_video = False
                for p in programs:
                    program_id = p['program_id']
                    service_name = p['tags'].get('service_name', 'Unknown')
                    
                    # Check if the program has any video streams
                    program_has_video = any(stream.get('codec_type') == 'video' for stream in p.get('streams', []))
                    
                    if program_has_video:
                        display_list.append(f"{service_name} (ID: {program_id}) [Video]")
                        has_video = True
                    else:
                        display_list.append(f"{service_name} (ID: {program_id}) [No Video]")
                
                self.channels[self.current_channel]["has_any_video_stream_detected"] = has_video # Store this info
                self.program_id_combo['values'] = display_list
                config = self.channels[self.current_channel]["config"] # Get config to load saved program_id
                selected_program_id = config.get("program_id") # Get saved program_id
                if selected_program_id:
                    found = False
                    for i, item in enumerate(display_list):
                        if f"(ID: {selected_program_id})" in item:
                            self.program_id_combo.current(i)
                            found = True
                            break
                    if not found:
                        self.program_id_combo.set("Previously selected service not found")
                elif display_list:
                    self.program_id_combo.current(0) # Select first if no previous selection
                else:
                    self.program_id_var.set("No services found")
            else:
                self.channels[self.current_channel]["has_any_video_stream_detected"] = False
                self.program_id_combo['values'] = []
                self.program_id_var.set("No services found")
        else:
            self.channels[self.current_channel]["has_any_video_stream_detected"] = False
            self.program_id_combo['values'] = []
            if self.input_type_var.get() == "SRT":
                self.program_id_var.set("N/A for SRT")
            else:
                self.program_id_var.set("N/A for this input type")

    def update_ui_for_channel(self):
        """
        Updates the state (enabled/disabled) of configuration widgets
        and triggers the update of status indicators based on the current channel's state.
        """
        if not self.current_channel:
            for child in self.config_frame.winfo_children():
                # Skip the action_buttons_frame as it's managed separately
                if child == self.action_buttons_frame:
                    continue
                if isinstance(child, (ttk.LabelFrame, ttk.Frame)):
                    for w in child.winfo_children():
                        if isinstance(w, (ttk.Entry, ttk.Button, ttk.Combobox)):
                            w.config(state='disabled')
                elif isinstance(child, ttk.Button):
                     child.config(state='disabled')
            # Disable preview buttons if no channel is selected
            self.preview_input_button.config(state='disabled')
            self.preview_output_button.config(state='disabled')
            return

        is_streaming = self.current_channel in self.processes
        current_input_status = self.channels[self.current_channel]["input_stream_status"]
        
        # Enable/disable config widgets based on streaming status
        for child in self.config_frame.winfo_children():
            # Skip the action_buttons_frame as its buttons are managed separately
            if child == self.action_buttons_frame: 
                continue 

            if isinstance(child, (ttk.LabelFrame, ttk.Frame)):
                for w in child.winfo_children():
                     if isinstance(w, (ttk.Entry, ttk.Combobox)):
                        w.config(state='disabled' if is_streaming else 'normal')
                        if isinstance(w, ttk.Combobox):
                            w.config(state='disabled'if is_streaming else 'readonly')
            elif isinstance(child, ttk.Entry):
                child.config(state='disabled' if is_streaming else 'normal')
            
        self.on_input_type_change()
        self.on_output_type_change() # Re-evaluate output type visibility
        
        self.start_button.config(state='disabled' if is_streaming else 'normal')
        self.stop_button.config(state='normal' if is_streaming else 'disabled')
        self.save_button.config(state='disabled' if is_streaming else 'normal') # Disable save while streaming
        self.global_refresh_button.config(state='normal') # Always enable global refresh button
        
        # Preview button logic:
        # Input Preview Button
        # The input preview button should be enabled if the input stream is 'available' (has packets), 'streaming', or 'starting' (UDP listener active).
        # It should be disabled if 'unavailable' (no packets/error) or 'unknown' (not UDP, or UDP listener not started).
        if current_input_status in ["available", "streaming", "starting"]:
            self.preview_input_button.config(state='normal')
            if self.preview_running and self.current_preview_type == "input":
                self.preview_input_button.config(text="Stop Input Preview", style='warning.TButton')
            else:
                self.preview_input_button.config(text="Preview Input", style='primary.TButton')
        else:
            self.preview_input_button.config(state='disabled', text="Preview Input", style='primary.TButton')

        # Output Preview Button (always enabled if a channel is selected, assuming output config is valid)
        if self.current_channel: # If a channel is selected, output preview is generally possible
            self.preview_output_button.config(state='normal')
            if self.preview_running and self.current_preview_type == "output":
                self.preview_output_button.config(text="Stop Output Preview", style='warning.TButton')
            else:
                self.preview_output_button.config(text="Preview Output", style='secondary.TButton')
        else:
            self.preview_output_button.config(state='disabled', text="Preview Output", style='secondary.TButton')
        

    def update_status_indicators(self):
        """
        Updates the color of the small canvas indicators (top bar) and the
        style/color of the channel name buttons (left bar) based on their status.
        """
        for name, channel_data in self.channels.items():
            input_stream_status = channel_data["input_stream_status"]
            is_streaming = name in self.processes # Check if FFmpeg process is running for this channel
            is_current_channel = (name == self.current_channel)

            # Determine color for status indicator (top block)
            canvas_color = "grey" # Default: Not configured / Unknown
            status_text = "Stream Not Started / Unknown Input" # Default tooltip text
            
            # Use the input_stream_status directly for both indicators' color logic
            # Prioritize statuses from most critical to least
            if input_stream_status == "unavailable":
                canvas_color = "red"
                status_text = "Input Missing / Unavailable"
            elif input_stream_status == "streaming": # This means FFmpeg is running AND input is healthy
                canvas_color = "green"
                status_text = "Input Present, Streaming"
            elif input_stream_status == "available":
                canvas_color = "yellow"
                status_text = "Input Present, Stream Stopped (Ready to Start)"
            elif input_stream_status == "scanning":
                canvas_color = "orange"
                status_text = "Scanning for Services..."
            elif input_stream_status == "starting": # New state for blue
                canvas_color = "blue" # ttkbootstrap 'info' style is light blue
                status_text = "Looking for Input (Waiting for Packets)"
            else: # "unknown" or any other unhandled status
                canvas_color = "grey"
                status_text = "Stream Not Started / Unknown Input"
            
            if name in self.status_indicators:
                self.status_indicators[name].config(bg=canvas_color)
                # Update tooltip text
                if name in self.status_tooltips:
                    self.status_tooltips[name].text = status_text # Update the tooltip text attribute
            
            # Determine style for channel button (left pane) based on the same input_stream_status
            if name in self.channel_buttons:
                btn = self.channel_buttons[name]
                
                if is_current_channel:
                    # If it's the current selected channel, use a filled style
                    if input_stream_status == "streaming":
                        btn_style = 'success.TButton' # Green for streaming
                    elif input_stream_status == "available":
                        btn_style = 'warning.TButton' # Yellow for input locked/available
                    elif input_stream_status == "scanning":
                        btn_style = 'info.TButton' # Light blue/cyan for scanning
                    elif input_stream_status == "unavailable":
                        btn_style = 'danger.TButton' # Red for input not locked/unavailable
                    elif input_stream_status == "starting":
                        btn_style = 'primary.TButton' # Blue for looking for input
                    else:
                        btn_style = 'primary.TButton' # Default for selected but unknown status
                else:
                    # If it's not the current selected channel, use outline style for non-streaming
                    if input_stream_status == "streaming":
                        btn_style = 'success.TButton' # Still green if streaming
                    elif input_stream_status == "unavailable":
                        btn_style = 'outline-danger.TButton' # Outline red
                    elif input_stream_status == "available":
                        btn_style = 'outline-warning.TButton' # Outline yellow
                    elif input_stream_status == "scanning":
                        btn_style = 'outline-info.TButton' # Outline light blue/cyan
                    elif input_stream_status == "starting":
                        btn_style = 'outline-primary.TButton' # Outline blue
                    else:
                        btn_style = 'outline-secondary.TButton' # Default outline style
                
                btn.config(text=channel_data["display_name"], style=btn_style)


    def get_input_url(self, config):
        """Constructs the full FFmpeg input URL based on the channel's configuration."""
        if config['input_type'] in ["HLS (M3U8)", "YouTube"]:
            return config['input_url']
        elif config['input_type'] == "UDP":
            return f"udp://@{config['input_ip']}:{config['input_port']}"
        elif config['input_type'] == "SRT":
            return f"srt://{config['input_ip']}:{config['input_port']}?mode={config['srt_mode']}"
        return ""

    def get_output_url(self, config):
        """Constructs the full FFmpeg output URL based on the channel's configuration."""
        output_type = config['output_type']
        output_ip = config['output_ip']
        output_port = config['output_port']
        output_url = config['output_url'] # For RTMP

        if output_type == "UDP":
            return f"udp://@{output_ip}:{output_port}"
        elif output_type == "SRT":
            mode = config['output_srt_mode']
            ip = output_ip
            if mode == "listener" and not ip:
                ip = "0.0.0.0" # Default to bind to all interfaces if listener and no IP specified
            
            srt_params = []
            if config.get('output_srt_latency') and config['output_srt_latency'] != "0":
                srt_params.append(f"latency={config['output_srt_latency']}")
            if config.get('output_srt_maxbw') and config['output_srt_maxbw'] != "0":
                srt_params.append(f"maxbw={config['output_srt_maxbw']}")
            if config.get('output_srt_tsbpdmode') in ["True", "False"]:
                srt_params.append(f"tsbpdmode={config['output_srt_tsbpdmode'].lower()}")
            if config.get('output_srt_sndbuf') and config['output_srt_sndbuf'] != "0":
                srt_params.append(f"sndbuf={config['output_srt_sndbuf']}")
            if config.get('output_srt_rcvbuf') and config['output_srt_rcvbuf'] != "0":
                srt_params.append(f"rcvbuf={config['output_srt_rcvbuf']}")

            params_string = ""
            if srt_params:
                params_string = f"?mode={mode}&{'&'.join(srt_params)}"
            else:
                params_string = f"?mode={mode}"

            return f"srt://{ip}:{output_port}{params_string}"
        elif output_type == "RTMP":
            return output_url
        elif output_type == "RTP":
            rtp_protocol = config.get("output_rtp_protocol", "udp")
            return f"{rtp_protocol}://{output_ip}:{output_port}"
        return ""
        
    def start_stream(self):
        """
        Initiates the FFmpeg streaming process for the current channel.
        Includes a check for input status before attempting to start.
        """
        if not self.current_channel:
            self.logger.error("Please select a channel to start streaming.")
            return
            
        self.save_current_config_to_memory() # Ensure latest UI values are saved
        save_channels_config(self.channels) # Persist channel config

        channel_name = self.current_channel
        current_channel_config = self.channels[channel_name]["config"]

        # --- Duplicate UDP Input Port Check ---
        if current_channel_config["input_type"] == "UDP":
            current_input_ip = current_channel_config["input_ip"]
            current_input_port = current_channel_config["input_port"]
            
            for name, channel_data in self.channels.items():
                if name != channel_name and name in self.processes: # Check other active channels
                    other_config = channel_data["config"]
                    if (other_config["input_type"] == "UDP" and
                        other_config["input_ip"] == current_input_ip and
                        other_config["input_port"] == current_input_port):
                        messagebox.showerror(
                            "Duplicate Input Port",
                            f"The input UDP address {current_input_ip}:{current_input_port} is already in use by active stream '{channel_data['display_name']}'.\n"
                            "Please choose a different input port or stop the conflicting stream."
                        )
                        self.logger.error(f"Attempted to start stream for '{channel_name}' on duplicate UDP input {current_input_ip}:{current_input_port}.")
                        return # Prevent starting the stream

        # Set stream_stop_requested to False as this is a user-initiated start
        self.stream_stop_requested[channel_name] = False

        # Immediately set status to "starting" (blue)
        self.master.after(0, self._set_input_stream_status, channel_name, "starting")

        thread = threading.Thread(target=self._start_stream_thread, args=(channel_name,))
        thread.daemon = True
        thread.start()

    def _start_stream_thread(self, channel_name, retry_count=0):
        """
        Worker thread function to prepare and execute the FFmpeg command.
        Handles YouTube URL resolution via yt-dlp if necessary.
        Includes retry logic.
        """
        config = self.channels[channel_name]["config"]
        
        input_url = self.get_input_url(config)
        output_url = self.get_output_url(config)

        if not input_url or not output_url:
            self.logger.error(f"[{channel_name}] Input or Output URL is not configured. Cannot start stream.")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
            return

        # Prepare popen_kwargs for subprocess.Popen
        popen_kwargs = {
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.PIPE
        }
        if os.name == 'nt': # Only add creationflags on Windows
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        if config['input_type'] == 'YouTube':
            self.logger.info(f"[{channel_name}] Looking up YouTube stream URL...")
            try:
                yt_dlp_cmd = ['yt-dlp', '-g', '-f', 'best', config['input_url']]
                # Use popen_kwargs to hide the yt-dlp console window
                result = subprocess.run(yt_dlp_cmd, capture_output=True, text=True, check=True, timeout=20, **popen_kwargs)
                input_url = result.stdout.strip()
                if not input_url:
                    raise ValueError("yt-dlp returned an empty URL.")
                self.logger.info(f"[{channel_name}] YouTube URL found. Starting ffmpeg.")
            except subprocess.CalledProcessError as e:
                # Log stderr for more specific yt-dlp errors
                self.logger.error(f"[{channel_name}] ERROR: yt-dlp failed to get YouTube stream URL. Stderr: {e.stderr.strip()}")
                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
                return
            except FileNotFoundError:
                self.logger.error(f"[{channel_name}] ERROR: yt-dlp executable not found. Please ensure yt-dlp is installed and in your PATH.")
                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
                return
            except Exception as e:
                self.logger.error(f"[{channel_name}] ERROR: Failed to get YouTube stream URL: {e}")
                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
                return

        command = [
            'ffmpeg',
            '-loglevel', self.app_config["ffmpeg_loglevel"],
        ]
        
        # Add global input options
        if config.get('input_analyzeduration') and config['input_analyzeduration'] != "0":
            command.extend(['-analyzeduration', config['input_analyzeduration']])
        if config.get('input_probesize') and config['input_probesize'] != "0":
            command.extend(['-probesize', config['input_probesize']])

        if config['local_bind_interface'] != "Auto" and config['input_type'] in ["UDP", "SRT"]:
            command.extend(['-bind_address', config['local_bind_interface']])

        command.extend([
            '-i', input_url
        ])
        
        if config["input_type"] == "UDP" and config["program_id"]:
            command.extend(['-map', f"0:p:{config['program_id']}"])
        else:
            command.extend(['-map', '0:v:0?', '-map', '0:a:0?']) # Use optional mapping

        command.extend([
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-b:v', f'{config["video_bitrate"]}k',
            '-c:a', 'copy',
            '-flags', '+global_header',
            '-g', '50',
            '-bsf:v', 'h264_mp4toannexb'
        ])
        
        # Output format and URL based on selected output type
        output_type = config['output_type']
        output_format = 'mpegts' # Default for UDP/SRT
        
        if output_type == "RTMP":
            output_format = 'flv' # RTMP uses FLV container
        elif output_type == "RTP":
            output_format = 'rtp' # RTP uses RTP protocol
            # Add RTP specific options if needed, e.g., payload type
            command.extend(['-payload_type', config.get('output_rtp_payload_type', '96')])
        
        # Add output specific options
        if config.get('output_max_delay') and config['output_max_delay'] != "0":
            command.extend(['-max_delay', config['output_max_delay']])
        
        if output_type == "UDP" and config.get('output_udp_pkt_size') and config['output_udp_pkt_size'] != "0":
            command.extend(['-pkt_size', config['output_udp_pkt_size']])

        command.extend(['-f', output_format, output_url])

        self.logger.info(f"Starting stream for '{channel_name}' (Attempt {retry_count + 1}/{self.app_config['retry_attempts'] + 1})...")
        
        # Log the FFmpeg command and explain advanced parameters
        ffmpeg_command_str = ' '.join(command)
        self.logger.info(f"[{channel_name}] FFmpeg Command: {ffmpeg_command_str}")
        
        advanced_params_explanation = []
        if config.get('input_analyzeduration') and config['input_analyzeduration'] != "0":
            advanced_params_explanation.append(f"-analyzeduration {config['input_analyzeduration']}: Increases the duration FFmpeg analyzes the input to detect stream properties, improving stream stability and reducing 'no data' errors.")
        if config.get('input_probesize') and config['input_probesize'] != "0":
            advanced_params_explanation.append(f"-probesize {config['input_probesize']}: Increases the amount of data FFmpeg reads from the input to determine stream format and codecs, crucial for complex or fragmented inputs.")
        if config.get('output_max_delay') and config['output_max_delay'] != "0":
            advanced_params_explanation.append(f"-max_delay {config['output_max_delay']}us: Sets the maximum demuxing delay in microseconds. A higher value can help buffer against input stream fluctuations, reducing stuttering.")
        if output_type == "SRT":
            if config.get('output_srt_latency') and config['output_srt_latency'] != "0":
                advanced_params_explanation.append(f"SRT latency={config['output_srt_latency']}ms: Buffers more data before playback, providing a larger window for retransmissions and smoothing out network jitter.")
            if config.get('output_srt_maxbw') and config['output_srt_maxbw'] != "0":
                advanced_params_explanation.append(f"SRT maxbw={config['output_srt_maxbw']}pkts/s: Sets the maximum bandwidth for SRT in packets per second (0 for unlimited).")
            if config.get('output_srt_tsbpdmode') == "True":
                advanced_params_explanation.append(f"SRT tsbpdmode=true: Time-Based Sender-Side Packet Delivery mode. Ensures packets are delivered based on their timestamps, improving synchronization and reducing jitter.")
            if config.get('output_srt_sndbuf') and config['output_srt_sndbuf'] != "0":
                advanced_params_explanation.append(f"SRT sndbuf={config['output_srt_sndbuf']} bytes: Sets the SRT send buffer size. A larger buffer can absorb more data before transmission, reducing drops.")
            if config.get('output_srt_rcvbuf') and config['output_srt_rcvbuf'] != "0":
                advanced_params_explanation.append(f"SRT rcvbuf={config['output_srt_rcvbuf']} bytes: Sets the SRT receive buffer size. A larger buffer helps absorb network fluctuations and retransmitted packets.")
        if output_type == "UDP" and config.get('output_udp_pkt_size') and config['output_udp_pkt_size'] != "0":
            advanced_params_explanation.append(f"-pkt_size {config['output_udp_pkt_size']}: Sets the UDP packet size. For MPEG-TS, 1316 bytes is common to fit within typical MTU, reducing fragmentation and potential loss.")

        if advanced_params_explanation:
            self.logger.info(f"[{channel_name}] Advanced FFmpeg Parameters for Robust Streaming:\n" + "\n".join(advanced_params_explanation))

        try:
            # UDP listener should already be running from __init__ or save_and_validate_config
            # if config['input_type'] == "UDP":
            #     if channel_name not in self.udp_listeners:
            #         udp_ip = config['input_ip']
            #         udp_port = int(config['input_port'])
            #         bind_address = config['local_bind_interface']
            #         if bind_address == "Auto":
            #             bind_address = "0.0.0.0"
            #         if not self._start_udp_listener(channel_name, udp_ip, udp_port, bind_address):
            #             self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
            #             return # Do not proceed with FFmpeg if UDP listener failed to start

            proc = subprocess.Popen(command, **popen_kwargs)

            self.processes[channel_name] = proc
            
            # Start stderr monitoring thread (for critical startup errors)
            stderr_monitor_thread = threading.Thread(target=self._monitor_ffmpeg_stderr, args=(proc, channel_name))
            stderr_monitor_thread.daemon = True
            stderr_monitor_thread.start()
            self.stderr_monitors[channel_name] = stderr_monitor_thread

            # Start process monitor thread
            monitor_thread = threading.Thread(target=self.monitor_process, args=(proc, channel_name))
            monitor_thread.daemon = True
            monitor_thread.start()

            self.master.after(0, self.update_ui_for_channel)
            self.logger.info(f"[{channel_name}] FFmpeg process started successfully.")
            # Trigger a refresh to update status immediately after starting stream
            self.master.after(0, self._refresh_all_stream_statuses)
            return # Exit loop if successful
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"[{channel_name}] Failed to start ffmpeg: {error_msg}")
            
            # Clean up any resources that might have been started
            if channel_name in self.stderr_monitors:
                del self.stderr_monitors[channel_name]
            
            if retry_count < self.app_config["retry_attempts"]:
                self.logger.warning(f"[{channel_name}] Retrying in {self.app_config['retry_delay_seconds']} seconds (Attempt {retry_count + 1}/{self.app_config['retry_attempts'] + 1})...")
                time.sleep(self.app_config['retry_delay_seconds'])
                # Re-call this function with incremented retry_count
                self._start_stream_thread(channel_name, retry_count + 1)
            else:
                self.logger.critical(f"[{channel_name}] ALARM: Max retry attempts reached. Stream will not start.")
                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
                messagebox.showerror("Stream Startup Failed",
                                     f"Failed to start stream for '{self.channels[channel_name]['display_name']}' after multiple retries.\n"
                                     "Please check input configuration and FFmpeg logs for details.")
                return

    def _start_udp_listener(self, channel_name, ip, port, bind_address):
        """Starts a UDP listener thread for the given channel."""
        self.logger.debug(f"[{channel_name}] Attempting to start UDP listener on {bind_address}:{port}...")
        if channel_name in self.udp_listeners:
            self.logger.debug(f"[{channel_name}] UDP listener already running for this channel. Skipping start.")
            # If listener is already running, ensure its status is correctly set based on recent packets
            self.master.after(0, self._refresh_all_stream_statuses) # Trigger refresh for this channel
            return True # Already running

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.0) # Small timeout for non-blocking receive
            
            # Allow reuse of address for quicker restarts
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # For multicast, if it's a multicast address
            if ip.startswith("224.") or ip.startswith("239."):
                self.logger.debug(f"[{channel_name}] Configuring socket for multicast.")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                socket.inet_aton(ip) + socket.inet_aton(bind_address))
                # Conditionally set SO_REUSEPORT only if it exists
                if hasattr(socket, 'SO_REUSEPORT'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1) 
                    self.logger.debug(f"[{channel_name}] SO_REUSEPORT enabled.")
                else:
                    self.logger.warning(f"[{channel_name}] socket.SO_REUSEPORT not available on this system. Skipping.")
            
            sock.bind((bind_address, port))
            self.logger.info(f"[{channel_name}] UDP listener successfully bound to {bind_address}:{port}")
            self.udp_listeners[channel_name] = sock
            self.udp_packet_timestamps[channel_name] = time.time() # Initialize timestamp
            self._set_input_stream_status(channel_name, "starting") # Set to blue immediately when listener starts

            # Use a threading.Event to signal the thread to stop
            stop_event = threading.Event()
            self.udp_listener_active_flags[channel_name] = stop_event

            udp_listener_thread = threading.Thread(target=self._udp_listener_thread, args=(channel_name, sock, stop_event))
            udp_listener_thread.daemon = True
            udp_listener_thread.start()
            self.udp_listener_threads[channel_name] = udp_listener_thread
            self.logger.debug(f"[{channel_name}] UDP listener thread started.")
            self.master.after(0, self._refresh_all_stream_statuses) # Trigger refresh after listener starts
            return True
        except OSError as e:
            error_message = f"[{channel_name}] Failed to bind UDP listener to {bind_address}:{port}: {e}"
            self.logger.error(error_message)
            if "address already in use" in str(e).lower():
                self.logger.error(f"[{channel_name}] Remedy: The port {port} is likely in use by another application or a lingering socket. Try changing the port or ensuring no other process is using it.")
            self._set_input_stream_status(channel_name, "unavailable") # Set unavailable if listener cannot even start
            if channel_name in self.udp_listeners:
                self.udp_listeners[channel_name].close()
                del self.udp_listeners[channel_name]
            return False
        except ValueError:
            self.logger.error(f"[{channel_name}] Invalid UDP port '{port}'. Setting status to unavailable.")
            self._set_input_stream_status(channel_name, "unavailable") # Set unavailable if listener cannot even start
            return False
        except Exception as e:
            self.logger.error(f"[{channel_name}] Unexpected error starting UDP listener: {e}. Setting status to unavailable.")
            self._set_input_stream_status(channel_name, "unavailable") # Set unavailable if listener cannot even start
            return False

    def _stop_udp_listener(self, channel_name):
        """Signals a UDP listener thread to stop and cleans up resources."""
        self.logger.debug(f"[{channel_name}] Request to stop UDP listener.")
        if channel_name in self.udp_listener_active_flags:
            self.logger.info(f"[{channel_name}] Signaling UDP listener thread to stop.")
            self.udp_listener_active_flags[channel_name].set() # Set the event to signal stop
            
            # Safely attempt to join and clean up the thread reference
            if channel_name in self.udp_listener_threads:
                thread_to_stop = self.udp_listener_threads[channel_name]
                if thread_to_stop.is_alive(): # Only try to join if it's still running
                    self.logger.debug(f"[{channel_name}] Attempting to join UDP listener thread.")
                    thread_to_stop.join(timeout=1) # Give it a moment to finish
                    if thread_to_stop.is_alive():
                        self.logger.warning(f"[{channel_name}] UDP listener thread did not stop gracefully after join.")
                del self.udp_listener_threads[channel_name] # Always remove from dict after trying to stop

            if channel_name in self.udp_listeners:
                try:
                    self.udp_listeners[channel_name].close()
                    self.logger.debug(f"[{channel_name}] UDP listener socket closed.")
                except Exception as e:
                    self.logger.error(f"[{channel_name}] Error closing UDP listener socket: {e}")
                del self.udp_listeners[channel_name]
            
            if channel_name in self.udp_packet_timestamps:
                del self.udp_packet_timestamps[channel_name]
            del self.udp_listener_active_flags[channel_name]
            
            # Do NOT set status to unknown here. Let _refresh_all_stream_statuses handle it
            # based on whether the FFmpeg process is running or not.
            self.master.after(0, self._refresh_all_stream_statuses) # Trigger refresh after listener stops
        else:
            self.logger.debug(f"[{channel_name}] No active UDP listener to stop.")


    def _udp_listener_thread(self, channel_name, sock, stop_event):
        """
        A dedicated thread to listen for UDP packets on a specific socket.
        Updates the udp_packet_timestamps for the channel.
        """
        self.logger.debug(f"[{channel_name}] UDP listener thread started for {sock.getsockname()}.")
        try:
            while not stop_event.is_set(): # Loop until stop event is set
                try:
                    data, addr = sock.recvfrom(2048) # Receive up to 2048 bytes (typical for TS packets)
                    self.udp_packet_timestamps[channel_name] = time.time()
                    self.logger.debug(f"[{channel_name}] Received UDP packet from {addr}. Timestamp updated.")
                except socket.timeout:
                    # No packet received within the timeout, continue loop
                    pass
                except Exception as e:
                    # Log other errors but don't necessarily stop the thread immediately unless critical
                    self.logger.error(f"[{channel_name}] UDP listener error: {e}")
                    # If the socket is genuinely broken, set status to unavailable
                    if isinstance(e, (socket.error, OSError)) and "forcibly closed" in str(e).lower():
                        self.logger.error(f"[{channel_name}] Remedy: UDP socket forcibly closed. This might indicate an external process interfering or a network issue.")
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                        break # Exit the loop on critical socket error
                time.sleep(0.01) # Small delay to prevent busy-waiting, but allow quick response to stop_event
        except Exception as thread_exception:
            self.logger.critical(f"[{channel_name}] ALARM: UDP listener thread crashed with unhandled exception: {thread_exception}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        finally:
            # Clean up socket when the loop exits (handled by _stop_udp_listener for main app flow)
            # This block is mostly for unexpected thread exits
            if sock:
                try:
                    sock.close()
                    self.logger.debug(f"[{channel_name}] UDP listener socket closed by thread exit.")
                except Exception as e:
                    self.logger.error(f"[{channel_name}] Error closing UDP listener socket on thread exit: {e}")
            self.logger.debug(f"[{channel_name}] UDP listener thread finished.")


    def _monitor_ffmpeg_stderr(self, proc, channel_name):
        """
        Monitors the stderr of an FFmpeg process for critical startup errors.
        This is primarily for initial connection/configuration issues.
        """
        self.logger.debug(f"[{channel_name}] Started stderr monitoring thread.")
        try:
            # Read the first line to check for version info
            first_line = proc.stderr.readline().decode('utf-8', errors='ignore').strip()
            if first_line and "ffmpeg version" not in first_line:
                self.logger.error(f"[{channel_name}][FFmpeg stderr] {first_line}. Setting status to unavailable.")
                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                return # Exit if it's a critical non-version error
            
            # Continue reading for other errors for a short period
            # Use a non-blocking read with a small timeout to avoid hanging
            start_time = time.time()
            while time.time() - start_time < 5: # Read stderr for 5 seconds
                line = proc.stderr.readline()
                if not line:
                    break # EOF
                decoded_line = line.decode('utf-8', errors='ignore').strip()
                if decoded_line:
                    self.logger.error(f"[{channel_name}][FFmpeg stderr] {decoded_line}")
                    # Add specific error patterns here if you want to react immediately
                    if any(pattern.search(decoded_line) for pattern in [
                        re.compile(r'Input/output error', re.IGNORECASE),
                        re.compile(r'No such file or directory', re.IGNORECASE),
                        re.compile(r'Connection refused', re.IGNORECASE),
                        re.compile(r'Network is unreachable', re.IGNORECASE),
                        re.compile(r'Failed to open', re.IGNORECASE),
                        re.compile(r'Protocol not found', re.IGNORECASE),
                        re.compile(r'Permission denied', re.IGNORECASE), # e.g., binding to an address without permission
                        re.compile(r'Invalid data found when processing input', re.IGNORECASE)
                    ]):
                        self.logger.error(f"[{channel_name}] Remedy: FFmpeg reported a critical input error. Check input URL/IP/Port, network connectivity, and file permissions.")
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                        break # Stop monitoring after a critical error
                time.sleep(0.01) # Small delay
        except Exception as e:
            self.logger.error(f"[{channel_name}] Error in stderr monitoring: {e}")
        finally:
            if proc.stderr:
                proc.stderr.close()
            self.logger.debug(f"[{channel_name}] Stderr monitoring thread finished.")
    

    def _monitor_ffmpeg_processes(self):
        """
        Global thread that periodically checks if FFmpeg processes are still running.
        If a process unexpectedly exits, it logs the event and updates the status.
        No automatic restarts are triggered by this monitor.
        """
        self.logger.info("Started global FFmpeg process monitor.")
        while True:
            for channel_name in list(self.processes.keys()): # Iterate over a copy
                proc = self.processes.get(channel_name)
                if proc and proc.poll() is not None: # Process has exited
                    if not self.stream_stop_requested.get(channel_name, False):
                        self.logger.error(f"[{channel_name}] ALARM: FFmpeg process unexpectedly exited (Return Code: {proc.returncode}).")
                        self.logger.error(f"[{channel_name}] Remedy: Stream process crashed. Check FFmpeg logs for errors. This could be due to invalid input, resource exhaustion, or FFmpeg command issues.")
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable") # Set red status
                    else:
                        self.logger.info(f"[{channel_name}] FFmpeg process exited as requested by user.")
                        self.master.after(0, self._set_input_stream_status, channel_name, "unknown") # Reset to grey
                    
                    # Clean up process reference
                    if channel_name in self.processes:
                        del self.processes[channel_name]
                    self.master.after(0, self.update_ui_for_channel) # Update UI
                    self.stream_stop_requested[channel_name] = False # Clear the flag
            
            time.sleep(self.app_config["ffmpeg_process_monitor_interval_seconds"])

    def _refresh_all_stream_statuses(self):
        """
        Manually triggered function to check and update the status of all streams.
        This function now provides more reliable status by checking both FFmpeg process state
        and UDP packet flow for UDP inputs.
        This function is called periodically and on manual refresh.
        """
        self.logger.debug("Refreshing all stream statuses...")
        current_time = time.time()

        for channel_name, channel_data in list(self.channels.items()):
            config = channel_data["config"]
            is_streaming = channel_name in self.processes # Is FFmpeg process running?
            
            new_status = "unknown" # Default status for non-streaming channels

            if is_streaming:
                # If FFmpeg process is running, the channel is "streaming" (green)
                # unless its input is UDP and packet loss is detected.
                input_is_healthy = True
                if config["input_type"] == "UDP":
                    if channel_name in self.udp_packet_timestamps:
                        last_udp_packet_time = self.udp_packet_timestamps[channel_name]
                        if (current_time - last_udp_packet_time) > self.app_config["udp_packet_timeout_seconds"]:
                            input_is_healthy = False
                            self.logger.warning(f"[{channel_name}] UDP input packet loss detected for running stream. Input deemed unhealthy.")
                            # self.logger.warning(f"[{channel_name}] Remedy: Check UDP source, network path, and firewall settings to ensure packets are reaching {config['input_ip']}:{config['input_port']}.")
                    else: # UDP listener not active for a streaming UDP channel (shouldn't happen if stream is running)
                        input_is_healthy = False
                        self.logger.error(f"[{channel_name}] UDP listener not active for running UDP stream. Input deemed unhealthy.")
                        self.logger.error(f"[{channel_name}] Remedy: The UDP listener for this channel is not running. This indicates an issue with listener startup or an unexpected shutdown. Restart the application or reconfigure the channel.")
                
                # For non-UDP streaming, we assume input is healthy unless FFmpeg stderr suggests otherwise.
                # The _monitor_ffmpeg_stderr thread handles immediate errors.
                # If input_is_healthy remains True, status is streaming.
                if input_is_healthy:
                    new_status = "streaming" # Green
                else:
                    new_status = "unavailable" # Red (input lost while streaming)
            else:
                # If not streaming, check special states first
                if channel_data["input_stream_status"] == "scanning":
                    new_status = "scanning" # Keep scanning status if ffprobe is running
                elif config["input_type"] == "UDP":
                    if channel_name in self.udp_listener_threads and self.udp_listener_threads[channel_name].is_alive():
                        # UDP listener is active, check if packets are coming in
                        if channel_name in self.udp_packet_timestamps and \
                           (current_time - self.udp_packet_timestamps[channel_name]) <= self.app_config["udp_packet_timeout_seconds"]:
                            new_status = "available" # Yellow (input present, ready to stream)
                        else:
                            new_status = "starting" # Blue (listener active, but no recent packets yet or just started)
                            self.logger.debug(f"[{channel_name}] UDP listener active but no recent packets. Status 'starting'.")
                    else: # UDP listener not running or not yet started for this channel
                        new_status = "unknown" # Grey
                        self.logger.debug(f"[{channel_name}] UDP listener not active. Status 'unknown'.")
                else: # Non-UDP input types
                    # For non-UDP types not streaming, assume 'available' if a URL is configured, else 'unknown'
                    if self.get_input_url(config):
                        new_status = "available" # Yellow
                    else:
                        new_status = "unknown" # Grey

            # Update the status only if it's different to avoid unnecessary UI updates
            if self.channels[channel_name]["input_stream_status"] != new_status:
                self.logger.info(f"[{channel_name}] Status changed from '{self.channels[channel_name]['input_stream_status']}' to '{new_status}'.")
                self.channels[channel_name]["input_stream_status"] = new_status
                # Trigger UI update after status change
                self.master.after(0, self.update_ui_for_channel)
                self.master.after(0, self.update_status_indicators) # Ensure indicators are updated
            else:
                self.logger.debug(f"[{channel_name}] Status remains '{new_status}'. No UI update needed.")


    def monitor_process(self, proc, channel_name):
        """
        Monitors a running FFmpeg process. When it exits, logs the event
        and cleans up process/log file references.
        The `_monitor_ffmpeg_processes` thread will detect the exit and update status.
        """
        proc.wait() # Wait for the process to terminate
        return_code = proc.returncode
        
        self.logger.debug(f"FFmpeg process for '{channel_name}' finished monitoring. Return code: {return_code}.")
        
        # Clean up resources associated with this process
        if channel_name in self.stderr_monitors:
            del self.stderr_monitors[channel_name]
        
        # The _monitor_ffmpeg_processes thread will detect the process exit and handle status updates.
        # No direct UI update or restart call here to avoid race conditions.

    def start_stream_internal(self, channel_name):
        """Internal method to start a stream, used by auto-start logic on app launch."""
        self.logger.debug(f"Internal start stream requested for '{channel_name}'.")
        # Temporarily set current_channel to the one being restarted if it's not already selected
        original_current_channel = self.current_channel
        if self.current_channel != channel_name:
            self.current_channel = channel_name
            self.load_channel_config() # Load config for the channel being restarted
        
        self.logger.info(f"[{channel_name}] Auto-starting stream on app launch...")
        self.start_stream() # Call the public start_stream method
        
        # Restore original current_channel if it was changed
        if original_current_channel and original_current_channel != channel_name:
            self.current_channel = original_current_channel
            self.load_channel_config() # Reload config for the originally selected channel

    def stop_stream_internal(self, channel_name, user_initiated=True):
        """Internal method to stop a stream."""
        self.logger.debug(f"Internal stop stream requested for '{channel_name}' (user_initiated={user_initiated}).")
        if channel_name not in self.processes:
            self.logger.warning(f"[{channel_name}] No active stream to stop internally.")
            return

        self.logger.info(f"[{channel_name}] Stopping stream internally (user_initiated={user_initiated})...")
        proc = self.processes[channel_name]
        
        # Set the flag to indicate if this was a user-initiated stop
        self.stream_stop_requested[channel_name] = user_initiated

        # Immediately remove from active processes and update UI for instant feedback
        if channel_name in self.processes:
            del self.processes[channel_name] 
        self.master.after(0, self.update_ui_for_channel) # Force UI refresh

        # Terminate the process in a separate thread to avoid blocking the GUI
        threading.Thread(target=self._terminate_process_thread, args=(proc, channel_name)).start()


    def stop_stream(self):
        """Public method to stop the FFmpeg process for the current channel (user-initiated)."""
        if not self.current_channel:
            self.logger.warning("No channel selected to stop stream for.")
            return

        self.logger.info(f"User requested to stop stream for '{self.current_channel}'.")
        self.stop_stream_internal(self.current_channel, user_initiated=True)


    def _terminate_process_thread(self, proc, channel_name):
        """Helper thread to safely terminate an FFmpeg process with an immediate kill fallback."""
        self.logger.debug(f"[{channel_name}] Terminating FFmpeg process thread initiated.")
        try:
            # First, try to terminate gracefully
            proc.terminate()
            proc.wait(timeout=5)
            if proc.poll() is None:
                # If still alive, send a stronger kill signal
                self.logger.warning(f"FFmpeg process for '{channel_name}' did not terminate gracefully. Forcing kill.")
                proc.kill()
            self.logger.info(f"FFmpeg process for '{channel_name}' terminated successfully.")
        except Exception as e:
            self.logger.error(f"Error terminating FFmpeg process for '{channel_name}': {e}")
        finally:
            # The monitor_process will handle final cleanup and status updates.
            # If it was a user-initiated stop, status will be set to 'unknown' by _monitor_ffmpeg_processes
            pass 

    def save_and_validate_config(self):
        """Saves the current channel's configuration and triggers a re-scan if input changed."""
        if not self.current_channel:
            self.logger.warning("No channel selected to save configuration for.")
            return

        # Capture current input config before saving
        old_config = self.channels[self.current_channel]["config"].copy()
        old_input_type = old_config.get("input_type")
        old_input_ip = old_config.get("input_ip")
        old_input_port = old_config.get("input_port")
        old_bind_interface = old_config.get("local_bind_interface")

        self.save_current_config_to_memory() # Save UI values to memory
        save_channels_config(self.channels) # Persist all channels to file
        self.logger.info(f"Configuration saved for '{self.channels[self.current_channel]['display_name']}'.")

        new_config = self.channels[self.current_channel]["config"]
        channel_name = self.current_channel # Capture for use in callbacks
        new_input_type = new_config.get("input_type")
        new_input_ip = new_config.get("input_ip")
        new_input_port = new_config.get("input_port")
        new_bind_interface = new_config.get("local_bind_interface")

        # Logic for managing UDP listener based on config changes
        if new_input_type == "UDP":
            if old_input_type != "UDP" or \
               new_input_ip != old_input_ip or \
               new_input_port != old_input_port or \
               new_bind_interface != old_bind_interface:
                
                self.logger.info(f"[{channel_name}] UDP input configuration changed. Restarting UDP listener and triggering re-scan.")
                self._stop_udp_listener(self.current_channel) # Stop old listener if it was UDP
                try:
                    udp_ip = new_config['input_ip']
                    udp_port = int(new_config['input_port'])
                    bind_address = new_config['local_bind_interface']
                    if bind_address == "Auto":
                        bind_address = "0.0.0.0"
                    self._start_udp_listener(self.current_channel, udp_ip, udp_port, bind_address)
                except ValueError:
                    self.logger.error(f"[{self.current_channel}] Invalid UDP port for listener on save: {new_config['input_port']}")
                    self._set_input_stream_status(self.current_channel, "unavailable")
                except Exception as e:
                    self.logger.error(f"[{self.current_channel}] Error restarting UDP listener on save: {e}")
                    self._set_input_stream_status(self.current_channel, "unavailable")
                
                self.scan_services() # Trigger scan after listener is potentially restarted
            else:
                self.logger.info(f"[{channel_name}] No significant UDP input configuration changes. Ensuring listener is active.")
                # If UDP config didn't change, but listener might have been stopped (e.g., after preview)
                if channel_name not in self.udp_listeners or not self.udp_listener_threads.get(channel_name, threading.Thread()).is_alive():
                    try:
                        udp_ip = new_config['input_ip']
                        udp_port = int(new_config['input_port'])
                        bind_address = new_config['local_bind_interface']
                        if bind_address == "Auto":
                            bind_address = "0.0.0.0"
                        self.logger.info(f"[{channel_name}] UDP listener was not active, restarting it after save.")
                        self._start_udp_listener(self.current_channel, udp_ip, udp_port, bind_address)
                    except Exception as e:
                        self.logger.error(f"[{channel_name}] Error ensuring UDP listener is active after save: {e}")
                        self._set_input_stream_status(self.current_channel, "unavailable")

        else: # New input type is non-UDP
            if old_input_type == "UDP":
                self.logger.info(f"[{channel_name}] Input type changed from UDP. Stopping UDP listener.")
                self._stop_udp_listener(self.current_channel)
            
            # For non-UDP types, if URL is configured, set to available, else unknown
            if self.get_input_url(new_config):
                self.logger.debug(f"[{channel_name}] Non-UDP input URL present. Setting status to 'available'.")
                self._set_input_stream_status(channel_name, "available")
            else:
                self.logger.debug(f"[{channel_name}] Non-UDP input URL missing. Setting status to 'unknown'.")
                self._set_input_stream_status(channel_name, "unknown")
            self.logger.debug(f"Input configuration changed for '{self.channels[self.current_channel]['display_name']}'. No scan needed for this input type.")
        
        # After saving, trigger a global refresh to update all statuses
        self.master.after(0, self._refresh_all_stream_statuses)


    def on_closing(self):
        """Handles the window closing event, ensuring all FFmpeg processes are terminated and configs are saved."""
        self.logger.info("Application is closing. Performing cleanup...")
        # Before saving channels config, ensure last_known_streaming_state is up-to-date for all channels
        for channel_name in list(self.channels.keys()):
            self.channels[channel_name]["last_known_streaming_state"] = channel_name in self.processes
        save_channels_config(self.channels)
        save_app_config(self.app_config)
        self.logger.info("Channel and application configurations saved.")

        # Terminate all running FFmpeg processes
        self.logger.info("Terminating all FFmpeg processes...")
        for channel_name, proc in list(self.processes.items()): # Iterate over a copy
            try:
                proc.terminate() # Send termination signal
                proc.wait(timeout=5) # Give it some time to exit gracefully
                if proc.poll() is None: # If still running, force kill
                    proc.kill()
                self.logger.info(f"FFmpeg process for '{channel_name}' terminated.")
            except Exception as e:
                self.logger.error(f"Error terminating FFmpeg process for '{channel_name}': {e}")
        
        # Stop and close all UDP listener sockets
        self.logger.info("Stopping all UDP listeners...")
        for channel_name in list(self.udp_listeners.keys()):
            self._stop_udp_listener(channel_name)

        # Stop preview if running
        self.logger.info("Stopping any active preview...")
        self._stop_preview_internal()

        self.master.destroy()
        self.logger.info("Application destroyed.")

    def scan_services(self, *args):
        """
        Initiates a scan for services/programs in the input UDP stream using ffprobe.
        Updates the input stream status and program selection combo.
        """
        if not self.current_channel or self.input_type_var.get() != "UDP":
            self.logger.warning("Scan for services is only applicable for UDP input type. Skipping scan.")
            # If not UDP, ensure status is not stuck on scanning/unavailable from previous state
            if self.current_channel:
                self._set_input_stream_status(self.current_channel, "unknown")
            return
        
        config = self.channels[self.current_channel]['config']
        self.save_current_config_to_memory()
        input_url = self.get_input_url(config)
        if not input_url:
            self.logger.error("Input address is required for scanning. Cannot proceed with ffprobe.")
            self._set_input_stream_status(self.current_channel, "unavailable") # Set red status
            return
        
        self.channels[self.current_channel]["input_stream_status"] = "scanning"
        self.update_status_indicators()
        self.logger.info(f"Scanning {input_url} for services using ffprobe...")
        
        thread = threading.Thread(target=self._run_ffprobe, args=(input_url,))
        thread.start()

    def _run_ffprobe(self, input_url):
        """
        Worker thread function to execute the ffprobe command.
        Parses the output to find programs/services in the stream.
        """
        channel_name = self.current_channel # Capture current channel name for thread safety
        self.logger.debug(f"[{channel_name}] ffprobe thread started for {input_url}.")
        try:
            command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_programs', '-show_streams', input_url] # Added -show_streams
            # Prepare popen_kwargs for subprocess.run
            popen_kwargs = {
                'capture_output': True,
                'text': True,
                'check': True,
                'timeout': 15
            }
            if os.name == 'nt': # Only add creationflags on Windows
                popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(command, **popen_kwargs)
            data = json.loads(result.stdout)
            programs = data.get('programs', [])
            streams = data.get('streams', []) # Get global streams for programs without explicit stream info
            
            # Enrich programs with stream information if not already present
            for p in programs:
                if 'streams' not in p:
                    # Attempt to find streams belonging to this program
                    p['streams'] = [s for s in streams if s.get('program_id') == p['program_id']]

            self.master.after(0, self._update_programs_list, programs, channel_name)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"[{channel_name}] ffprobe failed: {e.stderr.strip()}")
            self.logger.error(f"[{channel_name}] Remedy: ffprobe could not analyze the input. Check input URL/IP/Port, ensure the stream is active, and FFmpeg/ffprobe are correctly installed.")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except json.JSONDecodeError as e:
            self.logger.error(f"[{channel_name}] Failed to parse ffprobe output: {e}. Output might be malformed or empty.")
            self.logger.error(f"[{channel_name}] Remedy: The input stream might not be a valid MPEG-TS or contains corrupted data. Verify the source stream's integrity.")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except FileNotFoundError:
            messagebox.showerror("Error", "ffprobe executable not found. Please ensure FFmpeg is installed and in your PATH.")
            self.logger.error("ffprobe executable not found. Remedy: Install FFmpeg and ensure its executable directory is in your system's PATH environmental variable.")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except Exception as e:
            self.logger.error(f"[{channel_name}] Unexpected error during ffprobe scan: {e}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        finally:
            self.logger.debug(f"[{channel_name}] ffprobe thread finished.")

    def _update_programs_list(self, programs, channel_name):
        """
        Updates the program selection combobox and input stream status
        based on the results of the ffprobe scan.
        """
        self.logger.debug(f"[{channel_name}] Updating programs list with {len(programs)} programs.")
        if not channel_name or channel_name not in self.channels: return
        
        if not programs:
            self.logger.info(f"[{channel_name}] No services/programs found in the stream.")
            if channel_name == self.current_channel:
                self.program_id_combo['values'] = []
                self.program_id_var.set("No services found")
            self._set_input_stream_status(channel_name, "unavailable")
            self.channels[channel_name]["has_any_video_stream_detected"] = False # No programs, so no video
            return
        
        self.logger.info(f"[{channel_name}] Found {len(programs)} services.")
        self.channels[channel_name]["programs"] = programs
        
        display_list = []
        has_any_video_stream = False
        for p in programs:
            program_id = p['program_id']
            service_name = p['tags'].get('service_name', 'Unknown')
            
            # Check if the program has any video streams
            program_has_video = any(stream.get('codec_type') == 'video' for stream in p.get('streams', []))
            
            if program_has_video:
                display_list.append(f"{service_name} (ID: {program_id}) [Video]")
                has_any_video_stream = True
            else:
                display_list.append(f"{service_name} (ID: {program_id}) [No Video]")
        
        self.channels[channel_name]["has_any_video_stream_detected"] = has_any_video_stream
        
        if channel_name == self.current_channel:
            self.program_id_combo['values'] = display_list
            config = self.channels[channel_name]["config"] # Get config to load saved program_id
            selected_program_id = config.get("program_id") # Get saved program_id
            if selected_program_id:
                found = False
                for i, item in enumerate(display_list):
                    if f"(ID: {selected_program_id})" in item:
                        self.program_id_combo.current(i)
                        found = True
                        break
                    if not found and display_list: # If previously selected ID not found, but other options exist
                        self.program_id_combo.current(0) # Select first available
                        self.logger.warning(f"[{channel_name}] Previously selected program ID {selected_program_id} not found. Selecting first available.")
                if not found: # If no options at all, or previously selected not found and no other options
                    self.program_id_combo.set("No services found")
            elif display_list:
                self.program_id_combo.current(0) # Select first if no previous selection
            else:
                self.program_id_var.set("No services found")
        
        self._set_input_stream_status(channel_name, "available")
        self.master.after(0, self._refresh_all_stream_statuses) # Trigger a global refresh after scan

    def _set_input_stream_status(self, channel_name, status):
        """Helper function to update a channel's input stream status and trigger UI refresh."""
        if channel_name in self.channels:
            # Only update if the status is actually changing to avoid unnecessary UI redraws
            if self.channels[channel_name]["input_stream_status"] != status:
                self.logger.debug(f"[{channel_name}] Setting input stream status to: {status}")
                self.channels[channel_name]["input_stream_status"] = status
                # Call update_status_indicators to update visual elements (colors)
                self.master.after(0, self.update_status_indicators)
                # Call update_ui_for_channel to re-evaluate button states (e.g., preview button)
                self.master.after(0, self.update_ui_for_channel)
            else:
                self.logger.debug(f"[{channel_name}] Status already '{status}'. No change needed.")

    # --- Preview Functions (using ffplay) ---
    def toggle_preview(self, preview_type):
        """Toggles the ffplay preview on or off for the specified type (input or output)."""
        if not self.current_channel:
            messagebox.showwarning("No Channel Selected", "Please select a channel to preview.")
            return

        # If a preview is already running, stop it regardless of type
        if self.preview_running:
            self.logger.info(f"Stopping current preview (Type: {self.current_preview_type}).")
            self._stop_preview_internal()
            # If the user clicked the *same* button again, they want to stop and not restart immediately.
            # If they clicked the *other* button, they want to stop the current one and start the new one.
            # We'll re-evaluate and potentially start the new one after stopping the old.
            if self.current_preview_type == preview_type:
                return # User clicked the active preview button to stop it.

        # If no preview was running, or a different one was just stopped, start the new one.
        self.logger.info(f"Starting new preview (Type: {preview_type}).")
        self._start_preview_internal(preview_type)

    def _start_preview_internal(self, preview_type):
        """Internal method to start the ffplay preview for the specified type."""
        config = self.channels.get(self.current_channel, {}).get("config", {})
        channel_name = self.current_channel # Capture for use in threads

        if preview_type == "input":
            source_url = self.get_input_url(config)
            title_suffix = "Input"
            message_prefix = "Input"
            
            # Temporarily stop the UDP listener for the current input channel
            # This is crucial because ffplay will try to bind to the same port
            # and cause an "address already in use" error.
            if config["input_type"] == "UDP":
                self.logger.info(f"[{channel_name}] Temporarily stopping UDP listener for input preview to free port for ffplay.")
                self._stop_udp_listener(channel_name)

            # Specific check for input preview if no video stream was detected
            if config["input_type"] == "UDP":
                selected_program_id = config.get("program_id")
                has_video_in_selected_program = False
                if selected_program_id:
                    for p in self.channels[self.current_channel].get("programs", []):
                        if str(p['program_id']) == selected_program_id:
                            has_video_in_selected_program = any(stream.get('codec_type') == 'video' for stream in p.get('streams', []))
                            break
                elif not selected_program_id and self.channels[self.current_channel]["programs"]:
                    # If no program selected, but programs exist, check if *any* has video
                    has_video_in_selected_program = self.channels[self.current_channel].get("has_any_video_stream_detected", False)

                if not has_video_in_selected_program:
                    messagebox.showwarning("No Video Stream Detected",
                                           f"The selected input stream for '{self.channels[self.current_channel]['display_name']}' "
                                           "does not appear to contain a video stream, or no program with video was selected.\n"
                                           "The preview window might appear blank or only play audio if available.")
                    self.logger.warning(f"[{channel_name}] Input preview started without detected video stream in selected program.")

        elif preview_type == "output":
            source_url = self.get_output_url(config)
            title_suffix = "Output"
            message_prefix = "Output"
        else:
            self.logger.error(f"Invalid preview type: {preview_type}")
            return

        if not source_url:
            messagebox.showerror("Preview Error", f"{message_prefix} URL/IP/Port is not configured for the selected channel.")
            self.logger.error(f"[{self.current_channel}] No valid {message_prefix.lower()} source configured for preview.")
            return

        self.logger.info(f"Starting ffplay preview for {self.current_channel} ({message_prefix}) from {source_url}")
        
        ffplay_command = [
            "ffplay",
            "-window_title", f"Live Preview: {self.channels[self.current_channel]['display_name']} ({title_suffix})",
            "-i", source_url,
            "-autoexit", # Auto-exit when stream ends or is interrupted
            "-x", "640", "-y", "360" # Set initial window size
        ]

        # Add global input options to ffplay for input preview
        if preview_type == "input":
            if config.get('input_analyzeduration') and config['input_analyzeduration'] != "0":
                ffplay_command.extend(['-analyzeduration', config['input_analyzeduration']])
            if config.get('input_probesize') and config['input_probesize'] != "0":
                ffplay_command.extend(['-probesize', config['input_probesize']])
            # Removed -map 0:p:PROGRAM_ID for ffplay as it causes "Option not found" error
            # ffplay_command.extend(['-map', f"0:v:0?", '-map', '0:a:0?']) # Use optional mapping for video/audio
        
        # For output preview, we might not need program mapping or probesize/analyzeduration
        # as ffplay is directly consuming the output stream.
        # However, if it's an RTP stream, we might need specific options.
        if preview_type == "output" and config['output_type'] == "RTP":
            ffplay_command.extend(['-rtp_payload_type', config.get('output_rtp_payload_type', '96')])

        # Conditionally add creation_flags only on Windows
        popen_kwargs = {
            'stdout': subprocess.DEVNULL, # Keep DEVNULL unless we suspect stdout is the issue
            'stderr': subprocess.PIPE
        }
        if os.name == 'nt':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        try:
            # Terminate any existing ffplay process first
            self._stop_preview_internal() # Ensure previous preview is fully stopped

            self.ffplay_process = subprocess.Popen(ffplay_command, **popen_kwargs)
            self.logger.debug(f"[{channel_name}] ffplay process started with PID: {self.ffplay_process.pid}")
            self.logger.debug(f"[{channel_name}] ffplay process poll() immediately after Popen: {self.ffplay_process.poll()}")
            
            time.sleep(0.1) # Small delay to allow window creation

            self.preview_running = True
            self.current_preview_type = preview_type # Store the current preview type
            
            # Start stderr monitoring for ffplay
            self.ffplay_stderr_monitor = threading.Thread(target=self._monitor_ffplay_stderr, args=(self.ffplay_process, self.current_channel))
            self.ffplay_stderr_monitor.daemon = True
            self.ffplay_stderr_monitor.start()

            self.master.after(0, self.update_ui_for_channel) # Update button states

            # Schedule auto-stop after the configured time
            self.preview_auto_stop_id = self.master.after(self.app_config['preview_auto_stop_seconds'] * 1000, self._stop_preview_internal)

        except FileNotFoundError:
            messagebox.showerror("Error", "ffplay executable not found. Please ensure FFmpeg is installed and in your PATH.")
            self.logger.error("ffplay executable not found. Remedy: Install FFmpeg and ensure its executable directory is in your system's PATH environmental variable.")
            self._stop_preview_internal() # Ensure state is reset
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start ffplay preview: {e}")
            self.logger.error(f"Failed to start ffplay preview: {e}. Remedy: Check ffplay command syntax, input/output URLs, and ensure no other process is using the preview port.")
            self._stop_preview_internal() # Ensure state is reset

    def _monitor_ffplay_stderr(self, proc, channel_name):
        """Monitors the stderr of the ffplay process for error messages."""
        self.logger.debug(f"[{channel_name}][FFplay stderr monitor] Started.")
        # Flag to track if video frames have started
        video_started = False
        try:
            for line in iter(proc.stderr.readline, b''):
                decoded_line = line.decode('utf-8', errors='ignore').strip()
                if decoded_line:
                    self.logger.debug(f"[{channel_name}][FFplay stderr] {decoded_line}") # Changed to debug for less clutter
                    # Check for "frame=" to detect if video frames are being received
                    if not video_started and "frame=" in decoded_line:
                        video_started = True
                        self.logger.info(f"[{channel_name}] ffplay: Video frames detected. Displaying video.")
        except Exception as e:
            self.logger.error(f"[{channel_name}][FFplay stderr monitor] Error reading stderr: {e}")
        finally:
            if proc.stderr:
                proc.stderr.close()
            self.logger.debug(f"[{channel_name}][FFplay stderr monitor] Finished.")


    def _stop_preview_internal(self):
        """Internal method to stop the ffplay preview process and cancels auto-stop timer."""
        self.logger.debug("Request to stop preview internally.")
        if not self.preview_running:
            self.logger.info("Preview is not running.")
            return

        self.preview_running = False
        self.current_preview_type = None # Clear the current preview type

        # Cancel any pending auto-stop timer
        if self.preview_auto_stop_id:
            self.master.after_cancel(self.preview_auto_stop_id)
            self.preview_auto_stop_id = None
            self.logger.debug("Cancelled preview auto-stop timer.")

        if self.ffplay_process and self.ffplay_process.poll() is None:
            self.logger.info("Terminating ffplay preview process.")
            try:
                self.ffplay_process.terminate()
                self.ffplay_process.wait(timeout=2)
                if self.ffplay_process.poll() is None:
                    self.ffplay_process.kill()
            except Exception as e:
                self.logger.error(f"Error terminating ffplay process: {e}")
            self.ffplay_process = None # Clear the reference
            
        # Ensure stderr monitor thread is joined if it exists
        if self.ffplay_stderr_monitor and self.ffplay_stderr_monitor.is_alive():
            self.logger.debug("Joining ffplay stderr monitor thread.")
            self.ffplay_stderr_monitor.join(timeout=1)
            if self.ffplay_stderr_monitor.is_alive():
                self.logger.warning("FFplay stderr monitor thread did not stop gracefully.")
        self.ffplay_stderr_monitor = None # Clear the reference

        self.logger.info("Preview stopped.")
        self.update_ui_for_channel() # Update button states

        # Restart UDP listener if it was stopped for input preview
        if self.current_channel and self.channels[self.current_channel]["config"]["input_type"] == "UDP":
            config = self.channels[self.current_channel]["config"]
            try:
                udp_ip = config['input_ip']
                udp_port = int(config['input_port'])
                bind_address = config['local_bind_interface']
                if bind_address == "Auto":
                    bind_address = "0.0.0.0"
                self.logger.info(f"[{self.current_channel}] Restarting UDP listener after preview stopped.")
                self._start_udp_listener(self.current_channel, udp_ip, udp_port, bind_address)
                # After restarting, status will be determined by _refresh_all_stream_statuses
            except ValueError:
                self.logger.error(f"[{self.current_channel}] Invalid UDP port for listener restart: {config['input_port']}. Listener may not restart correctly.")
            except Exception as e:
                self.logger.error(f"[{self.current_channel}] Error restarting UDP listener after preview: {e}. Listener may not restart correctly.")

    def _update_system_metrics(self):
        """Fetches and updates CPU, RAM, and Network usage in the UI."""
        # CPU Usage
        cpu_percent = psutil.cpu_percent(interval=None) # Non-blocking call
        self.cpu_pb['value'] = cpu_percent
        self._set_progressbar_bootstyle(self.cpu_pb, cpu_percent)
        self.cpu_pb_label.config(text=f"CPU: {cpu_percent:.1f}%")

        # RAM Usage
        ram_percent = psutil.virtual_memory().percent
        self.ram_pb['value'] = ram_percent
        self._set_progressbar_bootstyle(self.ram_pb, ram_percent)
        self.ram_pb_label.config(text=f"RAM: {ram_percent:.1f}%")

        # Network Usage (Bytes/second, then converted to Mbps for percentage)
        current_net_io = psutil.net_io_counters()
        current_time = time.time()

        time_diff = current_time - self.last_net_time
        if time_diff > 0:
            bytes_sent_diff = current_net_io.bytes_sent - self.last_net_bytes_sent
            bytes_recv_diff = current_net_io.bytes_recv - self.last_net_bytes_recv

            # Convert bytes/second to Mbps
            upload_speed_mbps = (bytes_sent_diff / time_diff) * 8 / (1024 * 1024)
            download_speed_mbps = (bytes_recv_diff / time_diff) * 8 / (1024 * 1024)
            
            # Use the higher of upload/download for network utilization
            current_net_speed_mbps = max(upload_speed_mbps, download_speed_mbps)
            
            network_max_mbps = self.app_config.get("network_max_bandwidth_mbps", 100) # Default to 100 Mbps
            if network_max_mbps <= 0: # Prevent division by zero
                network_utilization_percent = 0
            else:
                network_utilization_percent = (current_net_speed_mbps / network_max_mbps) * 100
            
            # Cap at 100%
            network_utilization_percent = min(100, network_utilization_percent)

            self.network_pb['value'] = network_utilization_percent
            self._set_progressbar_bootstyle(self.network_pb, network_utilization_percent)
            self.network_pb_label.config(text=f"NW: {current_net_speed_mbps:.1f}Mbps") # Changed label to Mbps

        self.last_net_bytes_sent = current_net_io.bytes_sent
        self.last_net_bytes_recv = current_net_io.bytes_recv
        self.last_net_time = current_time

        # Schedule the next update
        self.master.after(2000, self._update_system_metrics) # Update every 2 seconds

    def _set_progressbar_bootstyle(self, progressbar_widget, value):
        """Sets the bootstyle of a Progressbar based on its value."""
        if value < 50:
            progressbar_widget.config(bootstyle="success")
        elif 50 <= value < 75: 
            progressbar_widget.config(bootstyle="info") 
        elif 75 <= value < 90: 
            progressbar_widget.config(bootstyle="warning")
        else: # 90% and above
            progressbar_widget.config(bootstyle="danger")


if __name__ == '__main__':
    root = ttkb.Window()
    app = FFmpegStreamerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

