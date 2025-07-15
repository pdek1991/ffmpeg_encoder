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
    "udp_check_interval_seconds": 30, # Interval for checking UDP packet flow
    "ffmpeg_process_monitor_interval_seconds": 5 # How often to check if FFmpeg process is still running
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
            "config": {
                "input_type": "UDP", "input_ip": "239.1.1.1", "input_port": str(1233 + i), "input_url": "",
                "output_type": "UDP", "output_ip": "239.2.2.2", "output_port": str(5677 + i), "output_url": "",
                "video_bitrate": "2000", "program_id": "",
                "srt_mode": "listener", # Input SRT mode
                "local_bind_interface": "Auto",
                "output_srt_mode": "caller", # Output SRT mode
                "output_rtp_protocol": "udp", # RTP protocol (udp/tcp)
                "output_rtp_payload_type": "96" # RTP payload type
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
    "output_rtp_protocol": "udp",
    "output_rtp_payload_type": "96"
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
        self.master.title("FFmpeg UDP Streamer")
        self.master.geometry("1200x800")
        ttkb.Style(theme=self.app_config["theme"])

        # Set window icon
        try:
            icon_path = "logo.ico"
            if os.path.exists(icon_path):
                self.master.iconbitmap(icon_path)
            else:
                self.logger.warning(f"Icon file '{icon_path}' not found. Using default Python icon.")
        except Exception as e:
            self.logger.error(f"Error setting window icon: {e}. Using default Python icon.")

        # Setup logging
        self._setup_logging()

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

        # Start the periodic packet flow and process checker thread
        self.packet_checker_thread = threading.Thread(target=self._check_stream_health)
        self.packet_checker_thread.daemon = True
        self.packet_checker_thread.start()

        # Main frame for the entire application layout
        main_frame = ttk.Frame(self.master, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # Left frame for channel list and selection
        left_frame = ttk.Frame(main_frame, width=250)
        left_frame.pack(side=LEFT, fill=Y, padx=(0, 10))
        
        ttk.Label(left_frame, text="Channels", font=("Helvetica", 14, "bold")).pack(pady=10)

        self.channel_list_frame = ttk.Frame(left_frame)
        self.channel_list_frame.pack(fill=BOTH, expand=True)
        self.channel_buttons = {}
        
        self._populate_channel_list()

        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        top_bar_frame = ttk.Frame(right_frame)
        top_bar_frame.pack(fill=X, pady=(0, 10))

        status_frame = ttk.Frame(top_bar_frame)
        status_frame.pack(side=LEFT, anchor='w')
        
        ttk.Label(status_frame, text="Stream Status:", font=("Helvetica", 12, "bold")).pack(side=LEFT, padx=(0,10))
        
        self.status_indicators_frame = ttk.Frame(status_frame)
        self.status_indicators_frame.pack(side=LEFT)
        self.status_indicators = {}
        self.create_status_indicators()

        self.logo_label = ttk.Label(top_bar_frame)
        self.logo_label.pack(side=RIGHT, anchor='ne', padx=10)
        self.load_logo()

        self.config_frame = ttk.LabelFrame(right_frame, text="Configuration", padding="15")
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

        # Schedule periodic UI updates
        self._schedule_ui_update()

    def _schedule_ui_update(self):
        """Schedules a periodic update of the UI status indicators."""
        self.update_status_indicators()
        self.master.after(1000, self._schedule_ui_update) # Update every 1 second

    def _setup_logging(self):
        """Sets up the application's logging system with timed rotating files."""
        self.logger = logging.getLogger("FFmpegStreamerApp")
        self.logger.setLevel(self.app_config["logging_level"])

        # Prevent duplicate handlers if called multiple times
        if not self.logger.handlers:
            # Create logs directory if it doesn't exist
            if not os.path.exists("logs"):
                os.makedirs("logs")

            # Console handler for immediate feedback
            console_handler = logging.StreamHandler()
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
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        self.logger.info("Application logging initialized.")

    def _populate_channel_list(self):
        """Populates the channel list buttons based on loaded channels."""
        for channel_name in self.channels.keys():
            btn = ttk.Button(self.channel_list_frame, text=self.channels[channel_name]["display_name"],
                             command=lambda name=channel_name: self.select_channel(name), style='secondary.TButton')
            btn.pack(fill=X, pady=2)
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
        
        for channel_name in self.channels.keys():
            canvas = tk.Canvas(self.status_indicators_frame, width=20, height=20, bg='grey', highlightthickness=0)
            canvas.pack(side=LEFT, padx=3)
            self.status_indicators[channel_name] = canvas

    def load_logo(self):
        """Loads 'logo.gif' and displays it in the top right corner."""
        try:
            logo_path = "logo.gif" # Changed from logo.png to logo.gif
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                img.thumbnail((150, 50), Image.Resampling.LANCZOS)
                self.logo_photo = ImageTk.PhotoImage(img)
                self.logo_label.config(image=self.logo_photo)
        except Exception as e:
            self.logger.error(f"Error loading logo: {e}")

    def create_config_widgets(self):
        """Initializes all input fields, comboboxes, and buttons in the configuration panel."""
        input_group = ttk.LabelFrame(self.config_frame, text="Input Configuration", padding="10")
        input_group.pack(fill=X, pady=5)
        input_group.columnconfigure(1, weight=1)

        ttk.Label(input_group, text="Channel Display Name:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.display_name_var = tk.StringVar()
        self.display_name_entry = ttk.Entry(input_group, textvariable=self.display_name_var)
        self.display_name_entry.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky='we')
        self.display_name_var.trace_add("write", self._on_display_name_change)

        ttk.Label(input_group, text="Input Type:").grid(row=1, column=0, padx=5, pady=5, sticky=W)
        self.input_type_var = tk.StringVar(value="UDP")
        self.input_type_combo = ttk.Combobox(input_group, textvariable=self.input_type_var, values=["UDP", "SRT", "HLS (M3U8)", "YouTube"], state='readonly', width=15)
        self.input_type_combo.grid(row=1, column=1, padx=5, pady=5, sticky=W)
        self.input_type_combo.bind("<<ComboboxSelected>>", self.on_input_type_change)

        self.input_ip_port_frame = ttk.Frame(input_group)
        self.input_ip_port_frame.grid(row=2, column=0, columnspan=3, sticky='we')
        ttk.Label(self.input_ip_port_frame, text="IP Address:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.input_ip_var = tk.StringVar()
        ttk.Entry(self.input_ip_port_frame, textvariable=self.input_ip_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        ttk.Label(self.input_ip_port_frame, text="Port:").grid(row=0, column=2, padx=5, pady=2, sticky=W)
        self.input_port_var = tk.StringVar()
        ttk.Entry(self.input_ip_port_frame, textvariable=self.input_port_var, width=10).grid(row=0, column=3, padx=5, pady=2, sticky=W)
        self.input_ip_port_frame.columnconfigure(1, weight=1)
        
        self.input_url_frame = ttk.Frame(input_group)
        ttk.Label(self.input_url_frame, text="URL:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.input_url_var = tk.StringVar()
        ttk.Entry(self.input_url_frame, textvariable=self.input_url_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        self.input_url_frame.columnconfigure(1, weight=1)

        self.srt_mode_frame = ttk.Frame(input_group)
        self.srt_mode_frame.grid(row=4, column=0, columnspan=3, sticky='we')
        ttk.Label(self.srt_mode_frame, text="SRT Mode:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.srt_mode_var = tk.StringVar(value="listener")
        self.srt_mode_combo = ttk.Combobox(self.srt_mode_frame, textvariable=self.srt_mode_var, values=["listener", "caller"], state='readonly', width=15)
        self.srt_mode_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)
        self.srt_mode_frame.columnconfigure(1, weight=1)

        self.local_bind_interface_frame = ttk.Frame(input_group)
        self.local_bind_interface_frame.grid(row=5, column=0, columnspan=3, sticky='we')
        ttk.Label(self.local_bind_interface_frame, text="Local Bind Interface:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.local_bind_interface_var = tk.StringVar(value="Auto")
        self.local_bind_interface_combo = ttk.Combobox(self.local_bind_interface_frame, textvariable=self.local_bind_interface_var, values=self.local_ip_addresses, state='readonly', width=20)
        self.local_bind_interface_combo.grid(row=0, column=1, padx=5, pady=2, sticky='we')
        self.local_bind_interface_frame.columnconfigure(1, weight=1)

        self.scan_button = ttk.Button(input_group, text="Scan for Services", command=self.scan_services)
        self.scan_button.grid(row=3, column=2, padx=5, pady=5, sticky=E)

        ttk.Label(input_group, text="Select Service/Program:").grid(row=3, column=0, padx=5, pady=5, sticky=W)
        self.program_id_var = tk.StringVar()
        self.program_id_combo = ttk.Combobox(input_group, textvariable=self.program_id_var, state='readonly')
        self.program_id_combo.grid(row=3, column=1, padx=5, pady=5, sticky='we')
        self.program_id_combo.set("Scan input to populate")

        # --- Output Configuration ---
        output_group = ttk.LabelFrame(self.config_frame, text="Output Configuration", padding="10")
        output_group.pack(fill=X, pady=5)
        output_group.columnconfigure(1, weight=1)

        ttk.Label(output_group, text="Output Type:").grid(row=0, column=0, padx=5, pady=5, sticky=W)
        self.output_type_var = tk.StringVar(value="UDP")
        # Added RTMP and RTP to output types
        self.output_type_combo = ttk.Combobox(output_group, textvariable=self.output_type_var, values=["UDP", "SRT", "RTMP", "RTP"], state='readonly', width=15)
        self.output_type_combo.grid(row=0, column=1, padx=5, pady=5, sticky=W)
        self.output_type_combo.bind("<<ComboboxSelected>>", self.on_output_type_change)

        # Output IP/Port Frame (for UDP, SRT, RTP)
        self.output_ip_port_frame = ttk.Frame(output_group)
        self.output_ip_port_frame.grid(row=1, column=0, columnspan=2, sticky='we')
        ttk.Label(self.output_ip_port_frame, text="IP Address:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_ip_var = tk.StringVar()
        ttk.Entry(self.output_ip_port_frame, textvariable=self.output_ip_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        ttk.Label(self.output_ip_port_frame, text="Port:").grid(row=0, column=2, padx=5, pady=2, sticky=W)
        self.output_port_var = tk.StringVar()
        ttk.Entry(self.output_ip_port_frame, textvariable=self.output_port_var, width=10).grid(row=0, column=3, padx=5, pady=2, sticky=W)
        self.output_ip_port_frame.columnconfigure(1, weight=1)

        # Output URL Frame (for RTMP)
        self.output_url_frame = ttk.Frame(output_group)
        ttk.Label(self.output_url_frame, text="URL:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_url_var = tk.StringVar()
        ttk.Entry(self.output_url_frame, textvariable=self.output_url_var).grid(row=0, column=1, padx=5, pady=2, sticky='we')
        self.output_url_frame.columnconfigure(1, weight=1)

        # Output SRT Mode Frame
        self.output_srt_mode_frame = ttk.Frame(output_group)
        ttk.Label(self.output_srt_mode_frame, text="SRT Mode:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_srt_mode_var = tk.StringVar(value="caller")
        self.output_srt_mode_combo = ttk.Combobox(self.output_srt_mode_frame, textvariable=self.output_srt_mode_var, values=["caller", "listener"], state='readonly', width=15)
        self.output_srt_mode_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)
        self.output_srt_mode_frame.columnconfigure(1, weight=1)

        # Output RTP Protocol Frame
        self.output_rtp_protocol_frame = ttk.Frame(output_group)
        ttk.Label(self.output_rtp_protocol_frame, text="RTP Protocol:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_rtp_protocol_var = tk.StringVar(value="udp")
        self.output_rtp_protocol_combo = ttk.Combobox(self.output_rtp_protocol_frame, textvariable=self.output_rtp_protocol_var, values=["udp", "tcp"], state='readonly', width=15)
        self.output_rtp_protocol_combo.grid(row=0, column=1, padx=5, pady=2, sticky=W)
        self.output_rtp_protocol_frame.columnconfigure(1, weight=1)

        # Output RTP Payload Type Frame
        self.output_rtp_payload_type_frame = ttk.Frame(output_group)
        ttk.Label(self.output_rtp_payload_type_frame, text="RTP Payload Type:").grid(row=0, column=0, padx=5, pady=2, sticky=W)
        self.output_rtp_payload_type_var = tk.StringVar(value="96")
        ttk.Entry(self.output_rtp_payload_type_frame, textvariable=self.output_rtp_payload_type_var, width=10).grid(row=0, column=1, padx=5, pady=2, sticky=W)
        self.output_rtp_payload_type_frame.columnconfigure(1, weight=1)


        ttk.Label(output_group, text="Video Bitrate (kbps):").grid(row=20, column=0, padx=5, pady=5, sticky=W) # Adjusted row
        self.video_bitrate_var = tk.StringVar(value="2000")
        self.video_bitrate_entry = ttk.Entry(output_group, textvariable=self.video_bitrate_var, width=15)
        self.video_bitrate_entry.grid(row=20, column=1, padx=5, pady=5, sticky=W) # Adjusted row
        
        # --- Control Buttons ---
        control_group = ttk.Frame(self.config_frame, padding="10")
        control_group.pack(fill=X, pady=10)
        
        self.save_button = ttk.Button(control_group, text="Save Configuration", command=self.save_and_validate_config, style='info.TButton')
        self.save_button.pack(side=LEFT, padx=5)

        # New Refresh Button
        self.refresh_button = ttk.Button(control_group, text="Refresh Status", command=self.manual_refresh_status, style='secondary.TButton')
        self.refresh_button.pack(side=LEFT, padx=5)

        self.start_button = ttk.Button(control_group, text="Start Stream", command=self.start_stream, style='primary.TButton')
        self.start_button.pack(side=RIGHT, padx=5)
        self.stop_button = ttk.Button(control_group, text="Stop Stream", command=self.stop_stream, style='warning.TButton')
        self.stop_button.pack(side=RIGHT, padx=5)
        
        self.on_input_type_change()
        self.on_output_type_change() # Call this to set initial visibility

    def manual_refresh_status(self):
        """Manually triggers an update of all channel status indicators."""
        self.logger.info("Manual status refresh triggered.")
        self.update_status_indicators()

    def _on_display_name_change(self, *args):
        """Updates the channel button text and internal data when the display name entry changes."""
        if self.current_channel:
            new_display_name = self.display_name_var.get()
            if self.current_channel in self.channel_buttons:
                self.channel_buttons[self.current_channel].config(text=new_display_name)
            self.channels[self.current_channel]["display_name"] = new_display_name
            self.update_status_indicators()

    def on_input_type_change(self, event=None):
        """Adjusts visibility of input fields (IP/Port vs. URL) and new SRT/Interface options based on selected input type."""
        input_type = self.input_type_var.get()
        if input_type in ["UDP", "SRT"]:
            self.input_url_frame.grid_forget()
            self.input_ip_port_frame.grid(row=2, column=0, columnspan=3, sticky='we')
            
            self.local_bind_interface_frame.grid(row=5, column=0, columnspan=3, sticky='we')
            
            if input_type == "SRT":
                self.srt_mode_frame.grid(row=4, column=0, columnspan=3, sticky='we')
                self.scan_button.config(state='disabled')
                self.program_id_combo.config(state='disabled')
                self.program_id_var.set("N/A for SRT")
            else: # UDP
                self.srt_mode_frame.grid_forget()
                self.scan_button.config(state='normal')
                self.program_id_combo.config(state='readonly')
                if self.program_id_var.get() == "N/A for SRT":
                    self.program_id_var.set("Scan input to populate")
        else: # HLS, YouTube
            self.input_ip_port_frame.grid_forget()
            self.input_url_frame.grid(row=2, column=0, columnspan=3, sticky='we')
            
            self.srt_mode_frame.grid_forget()
            self.local_bind_interface_frame.grid_forget()

            self.scan_button.config(state='disabled')
            self.program_id_combo.config(state='disabled')
            self.program_id_var.set("N/A for this input type")

    def on_output_type_change(self, event=None):
        """Adjusts visibility of output fields based on selected output type."""
        output_type = self.output_type_var.get()

        # Hide all output-specific frames first
        self.output_ip_port_frame.grid_forget()
        self.output_url_frame.grid_forget()
        self.output_srt_mode_frame.grid_forget()
        self.output_rtp_protocol_frame.grid_forget()
        self.output_rtp_payload_type_frame.grid_forget()

        # Show relevant frames based on selection
        if output_type == "UDP":
            self.output_ip_port_frame.grid(row=1, column=0, columnspan=2, sticky='we')
            self.video_bitrate_entry.grid(row=20, column=1, padx=5, pady=5, sticky=W) # Ensure bitrate is visible
            self.video_bitrate_entry.config(state='normal')
        elif output_type == "SRT":
            self.output_ip_port_frame.grid(row=1, column=0, columnspan=2, sticky='we')
            self.output_srt_mode_frame.grid(row=2, column=0, columnspan=2, sticky='we')
            self.video_bitrate_entry.grid(row=20, column=1, padx=5, pady=5, sticky=W) # Ensure bitrate is visible
            self.video_bitrate_entry.config(state='normal')
        elif output_type == "RTMP":
            self.output_url_frame.grid(row=1, column=0, columnspan=2, sticky='we')
            self.video_bitrate_entry.grid(row=20, column=1, padx=5, pady=5, sticky=W) # Ensure bitrate is visible
            self.video_bitrate_entry.config(state='normal')
        elif output_type == "RTP":
            self.output_ip_port_frame.grid(row=1, column=0, columnspan=2, sticky='we')
            self.output_rtp_protocol_frame.grid(row=2, column=0, columnspan=2, sticky='we')
            self.output_rtp_payload_type_frame.grid(row=3, column=0, columnspan=2, sticky='we')
            self.video_bitrate_entry.grid(row=20, column=1, padx=5, pady=5, sticky=W) # Ensure bitrate is visible
            self.video_bitrate_entry.config(state='normal')

        # Adjust grid row for video bitrate based on what's visible
        # This is a bit of a hack, better to use a consistent grid manager or pack
        # For now, manually adjust based on the last row used by output options
        current_row = 1
        if output_type in ["UDP", "SRT", "RTP"]:
            current_row += 1 # For output_ip_port_frame
        if output_type == "SRT":
            current_row += 1 # For output_srt_mode_frame
        if output_type == "RTP":
            current_row += 2 # For output_rtp_protocol_frame and output_rtp_payload_type_frame
        if output_type == "RTMP":
            current_row += 1 # For output_url_frame
        
        self.video_bitrate_entry.grid(row=current_row + 1, column=1, padx=5, pady=5, sticky=W)
        ttk.Label(self.video_bitrate_entry.master, text="Video Bitrate (kbps):").grid(row=current_row + 1, column=0, padx=5, pady=5, sticky=W)


    def select_channel(self, channel_name):
        """
        Handles channel selection from the left sidebar.
        Saves current config, loads new channel's config, and updates UI.
        """
        if self.current_channel:
            self.save_current_config_to_memory()
            # Stop UDP listener for the previously selected channel if it was UDP
            if self.channels[self.current_channel]["config"]["input_type"] == "UDP":
                self._stop_udp_listener(self.current_channel)

        self.current_channel = channel_name
        self.app_config["last_selected_channel"] = channel_name # Update last selected channel
        save_app_config(self.app_config) # Save config immediately

        self.config_frame.config(text=f"Configuration for {self.channels[channel_name]['display_name']}")
        self.load_channel_config()
        self.update_ui_for_channel()

        # Start UDP listener for the newly selected channel if it's UDP
        if self.channels[self.current_channel]["config"]["input_type"] == "UDP":
            config = self.channels[self.current_channel]["config"]
            try:
                udp_ip = config['input_ip']
                udp_port = int(config['input_port'])
                bind_address = config['local_bind_interface']
                if bind_address == "Auto":
                    bind_address = "0.0.0.0"
                self._start_udp_listener(channel_name, udp_ip, udp_port, bind_address)
            except ValueError:
                self.logger.error(f"[{channel_name}] Invalid UDP port for listener: {config['input_port']}")
                self._set_input_stream_status(channel_name, "unavailable")
            except Exception as e:
                self.logger.error(f"[{channel_name}] Error starting UDP listener on channel select: {e}")
                self._set_input_stream_status(channel_name, "unavailable")


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
        config["output_rtp_protocol"] = self.output_rtp_protocol_var.get() # RTP protocol
        config["output_rtp_payload_type"] = self.output_rtp_payload_type_var.get() # RTP payload type

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
        self.output_rtp_protocol_var.set(config.get("output_rtp_protocol", "udp")) # RTP protocol
        self.output_rtp_payload_type_var.set(config.get("output_rtp_payload_type", "96")) # RTP payload type
        
        self.on_input_type_change()
        self.on_output_type_change() # Call this to set visibility based on loaded type

        programs = channel_data.get("programs", [])
        if self.input_type_var.get() == "UDP":
            if programs:
                display_list = [f"{p['tags'].get('service_name', 'Unknown')} (ID: {p['program_id']})" for p in programs]
                self.program_id_combo['values'] = display_list
                selected_program_id = config.get("program_id")
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
                    self.program_id_combo.current(0)
                else:
                    self.program_id_var.set("No services found")
            else:
                self.program_id_combo['values'] = []
                self.program_id_var.set("No services found")
        else:
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
                if isinstance(child, (ttk.LabelFrame, ttk.Frame)):
                    for w in child.winfo_children():
                        if isinstance(w, (ttk.Entry, ttk.Button, ttk.Combobox)):
                            w.config(state='disabled')
                elif isinstance(child, ttk.Button):
                     child.config(state='disabled')
            return

        is_streaming = self.current_channel in self.processes
        
        for child in self.config_frame.winfo_children():
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
        self.refresh_button.config(state='normal') # Always enable refresh button

        self.update_status_indicators()

    def update_status_indicators(self):
        """
        Updates the color of the small canvas indicators (top bar) and the
        style/color of the channel name buttons (left bar) based on their status.
        """
        for name, channel_data in self.channels.items():
            input_stream_status = channel_data["input_stream_status"]
            is_streaming = name in self.processes

            # Determine color for status indicator (top block)
            canvas_color = "grey" # Default: Not configured / Unknown
            
            # Prioritize "unavailable" (red) if input stream is unhealthy
            if input_stream_status == "unavailable":
                canvas_color = "red"
            elif is_streaming:
                canvas_color = "green" # Actively streaming
            elif input_stream_status == "available":
                canvas_color = "yellow" # Input available/locked (after successful scan or explicit setting)
            elif input_stream_status == "scanning":
                canvas_color = "orange" # Currently scanning
            
            if name in self.status_indicators:
                self.status_indicators[name].config(bg=canvas_color)
            
            # Determine style for channel button (left pane)
            if name in self.channel_buttons:
                btn = self.channel_buttons[name]
                btn_style = 'secondary.TButton' # Default style (grey)

                if input_stream_status == "unavailable":
                    btn_style = 'danger.TButton' # Red for input not locked/unavailable (filled)
                elif is_streaming:
                    btn_style = 'success.TButton' # Green for streaming
                elif input_stream_status == "available":
                    btn_style = 'warning.TButton' # Yellow for input locked/available (filled)
                elif input_stream_status == "scanning":
                    btn_style = 'info.TButton' # Light blue/cyan for scanning (filled) - to differentiate from yellow
                
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
            # SRT output can also be caller or listener, typically caller for pushing
            return f"srt://{output_ip}:{output_port}?mode={config['output_srt_mode']}"
        elif output_type == "RTMP":
            return output_url
        elif output_type == "RTP":
            # RTP requires a specific format, often with a payload type and protocol
            # Example: rtp://destination_ip:port?pkt_size=1316&localport=local_port
            # For simplicity, we'll use a basic RTP URL, assuming common defaults
            # FFmpeg typically handles the RTP header details.
            # The local_bind_interface might be relevant here as well for binding.
            # For RTP, you might also need an SDP file or specific FFmpeg options.
            # This is a basic example; complex RTP setups might need more.
            rtp_protocol = config.get("output_rtp_protocol", "udp")
            # For RTP, FFmpeg often expects the destination IP and port directly.
            # The payload type is usually handled by the -payload_type option or inferred.
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

        # Common creation flags for hiding console windows on Windows
        creation_flags = 0
        if os.name == 'nt': # Check if OS is Windows
            creation_flags = subprocess.CREATE_NO_WINDOW

        if config['input_type'] == 'YouTube':
            self.logger.info(f"[{channel_name}] Looking up YouTube stream URL...")
            try:
                yt_dlp_cmd = ['yt-dlp', '-g', '-f', 'best', config['input_url']]
                # Use creationflags to hide the yt-dlp console window
                result = subprocess.run(yt_dlp_cmd, capture_output=True, text=True, check=True, timeout=20, creationflags=creation_flags)
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
            '-analyzeduration', '10M',
            '-probesize', '10M',
        ]

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

        command.extend(['-f', output_format, output_url])

        self.logger.info(f"Starting stream for '{channel_name}' (Attempt {retry_count + 1}/{self.app_config['retry_attempts'] + 1})...")
        self.logger.info(f"FFmpeg Command: {' '.join(command)}")

        try:
            # Ensure UDP listener is started before FFmpeg for UDP inputs
            if config['input_type'] == "UDP":
                if channel_name not in self.udp_listeners:
                    udp_ip = config['input_ip']
                    udp_port = int(config['input_port'])
                    bind_address = config['local_bind_interface']
                    if bind_address == "Auto":
                        bind_address = "0.0.0.0"
                    if not self._start_udp_listener(channel_name, udp_ip, udp_port, bind_address):
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                        return # Do not proceed with FFmpeg if UDP listener failed to start

            # Conditionally pass creation_flags only on Windows
            if os.name == 'nt':
                proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creation_flags)
            else:
                proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

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
            # Set status to available if it's not UDP (UDP status is managed by packet flow)
            if config['input_type'] != "UDP":
                self.master.after(0, self._set_input_stream_status, channel_name, "available")
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
        if channel_name in self.udp_listeners:
            self.logger.info(f"[{channel_name}] UDP listener already running. Skipping start.")
            return True # Already running

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.0) # Small timeout for non-blocking receive
            
            # Allow reuse of address for quicker restarts
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # For multicast, if it's a multicast address
            if ip.startswith("224.") or ip.startswith("239."):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                socket.inet_aton(ip) + socket.inet_aton(bind_address))
                # Conditionally set SO_REUSEPORT only if it exists
                if hasattr(socket, 'SO_REUSEPORT'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1) 
                else:
                    self.logger.warning(f"[{channel_name}] socket.SO_REUSEPORT not available on this system. Skipping.")
            
            sock.bind((bind_address, port))
            self.logger.info(f"[{channel_name}] UDP listener bound to {bind_address}:{port}")
            self.udp_listeners[channel_name] = sock
            self.udp_packet_timestamps[channel_name] = time.time() # Initialize timestamp

            # Use a threading.Event to signal the thread to stop
            stop_event = threading.Event()
            self.udp_listener_active_flags[channel_name] = stop_event

            udp_listener_thread = threading.Thread(target=self._udp_listener_thread, args=(channel_name, sock, stop_event))
            udp_listener_thread.daemon = True
            udp_listener_thread.start()
            self.udp_listener_threads[channel_name] = udp_listener_thread
            return True
        except OSError as e:
            self.logger.error(f"[{channel_name}] Failed to bind UDP listener to {bind_address}:{port}: {e}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
            if channel_name in self.udp_listeners:
                self.udp_listeners[channel_name].close()
                del self.udp_listeners[channel_name]
            return False
        except ValueError:
            self.logger.error(f"[{channel_name}] Invalid UDP port: {port}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
            return False
        except Exception as e:
            self.logger.error(f"[{channel_name}] Error starting UDP listener: {e}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
            return False

    def _stop_udp_listener(self, channel_name):
        """Signals a UDP listener thread to stop and cleans up resources."""
        if channel_name in self.udp_listener_active_flags:
            self.logger.info(f"[{channel_name}] Signaling UDP listener thread to stop.")
            self.udp_listener_active_flags[channel_name].set() # Set the event to signal stop
            
            # Give the thread a moment to react and close its socket
            if channel_name in self.udp_listener_threads:
                self.udp_listener_threads[channel_name].join(timeout=1) # Wait for thread to finish
                if self.udp_listener_threads[channel_name].is_alive():
                    self.logger.warning(f"[{channel_name}] UDP listener thread did not stop gracefully.")
                del self.udp_listener_threads[channel_name]

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
                    # self.logger.debug(f"[{channel_name}] Received UDP packet from {addr}. Timestamp updated.")
                except socket.timeout:
                    # No packet received within the timeout, continue loop
                    pass
                except Exception as e:
                    # Log other errors but don't necessarily stop the thread immediately unless critical
                    self.logger.error(f"[{channel_name}] UDP listener error: {e}")
                    # If the socket is genuinely broken, set status to unavailable
                    if isinstance(e, (socket.error, OSError)) and "forcibly closed" in str(e).lower():
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
                        re.compile(r'Invalid data found when processing input', re.IGNORECASE),
                        # New patterns for H.264 errors
                        re.compile(r'no frame!', re.IGNORECASE),
                        re.compile(r'non-existing PPS \d+ referenced', re.IGNORECASE),
                        re.compile(r'Error while decoding', re.IGNORECASE), # General decoding error
                        re.compile(r'missing picture in access unit', re.IGNORECASE) # Another common H.264 error
                    ]):
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                        break # Stop monitoring after a critical error
                time.sleep(0.01) # Small delay
        except Exception as e:
            self.logger.error(f"[{channel_name}] Error in stderr monitoring: {e}")
        finally:
            if proc.stderr:
                proc.stderr.close()
            self.logger.debug(f"[{channel_name}] Stderr monitoring thread finished.")
    

    def _check_stream_health(self):
        """
        Global thread that periodically checks input stream health for all channels.
        For UDP inputs, it relies on the UDP listener's packet reception.
        For other input types (SRT, HLS, YouTube), it relies on FFmpeg's process state.
        This check runs at a configurable interval and triggers auto-restarts.
        """
        self.logger.info("Started global stream health checker.")
        while True:
            current_time = time.time()
            
            for channel_name, channel_data in list(self.channels.items()):
                config = channel_data["config"]
                current_input_status = channel_data["input_stream_status"]
                is_streaming = channel_name in self.processes
                
                # --- UDP Input Stream Health Check ---
                if config["input_type"] == "UDP":
                    if channel_name in self.udp_packet_timestamps:
                        last_udp_packet_time = self.udp_packet_timestamps[channel_name]
                        if (current_time - last_udp_packet_time) > self.app_config["udp_packet_timeout_seconds"]:
                            # UDP packet loss detected
                            if current_input_status != "unavailable":
                                self.logger.error(f"[{channel_name}] ALARM: UDP packet loss detected. No packets received in over {self.app_config['udp_packet_timeout_seconds']} seconds. Setting status to unavailable (red).")
                                self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                                # If streaming and input is lost, try to restart the stream
                                if is_streaming and not self.stream_stop_requested.get(channel_name, False):
                                    self.logger.warning(f"[{channel_name}] UDP input lost while streaming. Attempting auto-restart.")
                                    self.master.after(0, self.stop_stream_internal, channel_name, True) # Stop and then restart
                        elif current_input_status == "unavailable":
                            # Packets resumed, change status back to available
                            self.logger.info(f"[{channel_name}] UDP input is now available (packets received). Setting status to available (yellow/green).")
                            self.master.after(0, self._set_input_stream_status, channel_name, "available")
                            # If streaming was down due to input, try to restart it now that input is back
                            if not is_streaming and not self.stream_stop_requested.get(channel_name, False):
                                self.logger.info(f"[{channel_name}] UDP input restored. Attempting auto-restart stream.")
                                self.master.after(0, self.start_stream_internal, channel_name)
                    elif channel_name in self.udp_listeners and current_input_status != "unavailable":
                        # Listener is active but no packets ever received
                        self.logger.info(f"[{channel_name}] UDP input has active listener but no packets received yet. Marking unavailable.")
                        self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
                        # If streaming and input never arrived, try to restart
                        if is_streaming and not self.stream_stop_requested.get(channel_name, False):
                            self.logger.warning(f"[{channel_name}] UDP input never arrived while streaming. Attempting auto-restart.")
                            self.master.after(0, self.stop_stream_internal, channel_name, True) # Stop and then restart
                
                # --- FFmpeg Process Health Check for all streams (UDP and non-UDP) ---
                if is_streaming:
                    proc = self.processes[channel_name]
                    if proc.poll() is not None: # Process has exited
                        if not self.stream_stop_requested.get(channel_name, False): # If not a user-initiated stop
                            self.logger.error(f"[{channel_name}] ALARM: FFmpeg process unexpectedly exited (Return Code: {proc.returncode}). Attempting auto-restart.")
                            self.master.after(0, self.start_stream_internal, channel_name) # Trigger restart
                        else:
                            self.logger.info(f"[{channel_name}] FFmpeg process exited as requested by user.")
                            self.master.after(0, self._set_input_stream_status, channel_name, "unknown") # Reset status
                            # Clear the stop requested flag after handling
                            self.stream_stop_requested[channel_name] = False
                        
                        # Clean up process reference immediately
                        if channel_name in self.processes:
                            del self.processes[channel_name]
                        self.master.after(0, self.update_ui_for_channel) # Update UI
                elif current_input_status == "unavailable" and not is_streaming and not self.stream_stop_requested.get(channel_name, False):
                    # If stream is not running and input is unavailable, and it wasn't a user stop,
                    # and it's not UDP (UDP is handled above), then it's stuck.
                    # This might happen for HLS/YouTube if FFmpeg failed and didn't restart.
                    if config["input_type"] != "UDP":
                        self.logger.warning(f"[{channel_name}] Non-UDP stream is down and input unavailable. Attempting auto-restart.")
                        self.master.after(0, self.start_stream_internal, channel_name)

            time.sleep(self.app_config["udp_check_interval_seconds"])


    def monitor_process(self, proc, channel_name):
        """
        Monitors a running FFmpeg process. When it exits, logs the event
        and cleans up process/log file references.
        This function is now simpler as `_check_stream_health` handles restarts.
        """
        proc.wait() # Wait for the process to terminate
        return_code = proc.returncode
        
        # Logging for process exit is now primarily handled by _check_stream_health
        # The actual restart logic is also in _check_stream_health
        self.logger.debug(f"FFmpeg process for '{channel_name}' finished monitoring. Return code: {return_code}.")
        
        # Clean up resources associated with this process
        if channel_name in self.stderr_monitors:
            del self.stderr_monitors[channel_name]
        
        # UDP listener cleanup is handled by _stop_udp_listener on channel select/app close
        # or by _terminate_process_thread if stop_stream was called.
        # Ensure the UDP listener is stopped if it was associated with this process
        if self.channels[channel_name]["config"]["input_type"] == "UDP":
            self._stop_udp_listener(channel_name)

        # The _check_stream_health thread will detect the process exit and handle restarts/status updates.
        # No direct UI update or restart call here to avoid race conditions.

    def start_stream_internal(self, channel_name):
        """Internal method to start a stream, used by auto-restart logic."""
        # Temporarily set current_channel to the one being restarted if it's not already selected
        original_current_channel = self.current_channel
        if self.current_channel != channel_name:
            self.current_channel = channel_name
            self.load_channel_config() # Load config for the channel being restarted
        
        self.logger.info(f"[{channel_name}] Auto-restarting stream...")
        self.start_stream() # Call the public start_stream method
        
        # Restore original current_channel if it was changed
        if original_current_channel and original_current_channel != channel_name:
            self.current_channel = original_current_channel
            self.load_channel_config() # Reload config for the originally selected channel

    def stop_stream_internal(self, channel_name, auto_restart_flag=False):
        """Internal method to stop a stream, used by auto-restart logic."""
        if channel_name not in self.processes:
            self.logger.warning(f"[{channel_name}] No active stream to stop internally.")
            return

        self.logger.info(f"[{channel_name}] Stopping stream internally (auto-restart_flag={auto_restart_flag})...")
        proc = self.processes[channel_name]
        
        # Set the flag to indicate this is not a user-initiated stop if auto_restart is true
        self.stream_stop_requested[channel_name] = not auto_restart_flag

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
        self.stop_stream_internal(self.current_channel, auto_restart_flag=False)


    def _terminate_process_thread(self, proc, channel_name):
        """Helper thread to safely terminate an FFmpeg process with an immediate kill fallback."""
        try:
            # First, try to terminate gracefully
            proc.terminate()
            proc.wait(timeout=2)
            if proc.poll() is None:
                # If still alive, send a stronger kill signal
                self.logger.warning(f"FFmpeg process for '{channel_name}' did not terminate gracefully. Forcing kill.")
                proc.kill()
            self.logger.info(f"FFmpeg process for '{channel_name}' terminated successfully.")
        except Exception as e:
            self.logger.error(f"Error terminating FFmpeg process for '{channel_name}': {e}")
        finally:
            # Ensure UDP listener is closed if it was active for this channel
            if self.channels[channel_name]["config"]["input_type"] == "UDP":
                self._stop_udp_listener(channel_name)
            # The monitor_process will handle final cleanup and status updates.

    def save_and_validate_config(self):
        """Saves the current channel's configuration and triggers a re-scan if input changed."""
        if not self.current_channel:
            self.logger.warning("No channel selected to save configuration for.")
            return

        # Capture current input config before saving
        old_config = self.channels[self.current_channel]["config"].copy()

        self.save_current_config_to_memory() # Save UI values to memory
        save_channels_config(self.channels) # Persist all channels to file
        self.logger.info(f"Configuration saved for '{self.channels[self.current_channel]['display_name']}'.")

        new_config = self.channels[self.current_channel]["config"]

        # If input type changed to UDP, or UDP IP/port changed, restart UDP listener and scan
        if new_config["input_type"] == "UDP":
            if old_config["input_type"] != "UDP" or \
               new_config["input_ip"] != old_config["input_ip"] or \
               new_config["input_port"] != old_config["input_port"] or \
               new_config["local_bind_interface"] != old_config["local_bind_interface"]:
                
                self.logger.info(f"Input configuration changed for '{self.channels[self.current_channel]['display_name']}'. Restarting UDP listener and triggering re-scan.")
                self._stop_udp_listener(self.current_channel) # Stop old listener
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
                self.logger.info(f"No significant UDP input configuration changes for '{self.channels[self.current_channel]['display_name']}'.")
        else: # Non-UDP input type
            # If changed from UDP to non-UDP, stop the UDP listener
            if old_config["input_type"] == "UDP":
                self._stop_udp_listener(self.current_channel)
            self.logger.info(f"Input configuration changed for '{self.channels[self.current_channel]['display_name']}'. No scan needed for this input type.")
            self.logger.info(f"Input configuration changed for '{self.channels[self.current_channel]['display_name']}'. No scan needed for this input type.")
            self._set_input_stream_status(self.current_channel, "unknown") # Reset status if input type changed


    def on_closing(self):
        """Handles the window closing event, ensuring all FFmpeg processes are terminated and configs are saved."""
        self.logger.info("Application is closing. Terminating FFmpeg processes and saving configurations...")
        # Save current channel's config before exiting
        if self.current_channel:
            self.save_current_config_to_memory()
        save_channels_config(self.channels)
        save_app_config(self.app_config)

        # Terminate all running FFmpeg processes
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
        for channel_name in list(self.udp_listeners.keys()):
            self._stop_udp_listener(channel_name)

        self.master.destroy()

    def scan_services(self, *args):
        """
        Initiates a scan for services/programs in the input UDP stream using ffprobe.
        Updates the input stream status and program selection combo.
        """
        if not self.current_channel or self.input_type_var.get() != "UDP":
            self.logger.warning("Scan for services is only applicable for UDP input type.")
            # If not UDP, ensure status is not stuck on scanning/unavailable from previous state
            if self.current_channel:
                self._set_input_stream_status(self.current_channel, "unknown")
            return
        
        config = self.channels[self.current_channel]['config']
        self.save_current_config_to_memory()
        input_url = self.get_input_url(config)
        if not input_url:
            self.logger.error("Input address is required for scanning.")
            self._set_input_stream_status(self.current_channel, "unavailable") # Set red status
            return
        
        self.channels[self.current_channel]["input_stream_status"] = "scanning"
        self.update_status_indicators()
        self.logger.info(f"Scanning {input_url} for services...")
        
        thread = threading.Thread(target=self._run_ffprobe, args=(input_url,))
        thread.start()

    def _run_ffprobe(self, input_url):
        """
        Worker thread function to execute the ffprobe command.
        Parses the output to find programs/services in the stream.
        """
        channel_name = self.current_channel # Capture current channel name for thread safety
        try:
            command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_programs', input_url]
            # Add creationflags to prevent console window for subprocess on Windows
            creation_flags = 0
            if os.name == 'nt': # Check if OS is Windows
                creation_flags = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=15, creationflags=creation_flags)
            data = json.loads(result.stdout)
            programs = data.get('programs', [])
            self.master.after(0, self._update_programs_list, programs, channel_name)
        except subprocess.CalledProcessError as e:
            self.logger.error(f"ffprobe failed for '{channel_name}': {e.stderr.strip()}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse ffprobe output for '{channel_name}': {e}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except FileNotFoundError:
            self.logger.error("ffprobe executable not found. Please ensure FFmpeg is installed and in your PATH.")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")
        except Exception as e:
            self.logger.error(f"Error during ffprobe scan for '{channel_name}': {e}")
            self.master.after(0, self._set_input_stream_status, channel_name, "unavailable")

    def _update_programs_list(self, programs, channel_name):
        """
        Updates the program selection combobox and input stream status
        based on the results of the ffprobe scan.
        """
        if not channel_name or channel_name not in self.channels: return
        
        if not programs:
            self.logger.info(f"No services/programs found in the stream for '{channel_name}'.")
            if channel_name == self.current_channel:
                self.program_id_combo['values'] = []
                self.program_id_var.set("No services found")
            self._set_input_stream_status(channel_name, "unavailable")
            return
        
        self.logger.info(f"Found {len(programs)} services for '{channel_name}'.")
        self.channels[channel_name]["programs"] = programs
        
        if channel_name == self.current_channel:
            display_list = [f"{p['tags'].get('service_name', 'Unknown')} (ID: {p['program_id']})" for p in programs]
            self.program_id_combo['values'] = display_list
            if display_list: self.program_id_combo.current(0)
            else: self.program_id_var.set("No services found")
        
        self._set_input_stream_status(channel_name, "available")

    def _set_input_stream_status(self, channel_name, status):
        """Helper function to update a channel's input stream status and trigger UI refresh."""
        if channel_name in self.channels:
            # Only update if the status is actually changing to avoid unnecessary UI redraws
            if self.channels[channel_name]["input_stream_status"] != status:
                self.channels[channel_name]["input_stream_status"] = status
                self.update_status_indicators()

if __name__ == '__main__':
    root = ttkb.Window()
    app = FFmpegStreamerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
