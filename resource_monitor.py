import psutil
import time
import csv
import os
from datetime import datetime

# --- Configuration ---
# Names of the processes to monitor.
# If you run the app as 'python encoder.py', use 'python.exe' or 'pythonw.exe'
# If you build it into an EXE with PyInstaller (e.g., encoder.exe), use that name.
# Always include 'ffmpeg.exe' as it's the core streaming process.
PROCESS_NAMES = ["encoder.exe", "python.exe", "pythonw.exe", "ffmpeg.exe", "ffprobe.exe"] 
MONITOR_INTERVAL_SECONDS = 5 # How often to collect data (in seconds)
OUTPUT_CSV_FILE = "resource_utilization_1stream.csv"
MONITOR_DURATION_MINUTES = 10 # How long to monitor for each test (e.g., 10 minutes)

def get_process_info(process_names):
    """
    Collects CPU and memory usage for specified process names.
    Returns total CPU percent and total memory percent.
    """
    total_cpu_percent = 0.0
    total_memory_percent = 0.0
    
    # Keep track of PIDs to avoid double-counting if a process name matches multiple times
    # e.g., if 'python.exe' is listed and there are multiple python processes
    monitored_pids = set() 

    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            if proc.info['name'] in process_names and proc.info['pid'] not in monitored_pids:
                total_cpu_percent += proc.cpu_percent(interval=None) # Non-blocking call
                total_memory_percent += proc.memory_percent()
                monitored_pids.add(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Process might have terminated between iter and access, or access denied
            pass
    return total_cpu_percent, total_memory_percent

def monitor_resources():
    """
    Monitors resource utilization over time and saves to a CSV file.
    """
    print(f"Starting resource monitoring. Data will be saved to '{OUTPUT_CSV_FILE}'")
    print(f"Monitoring for process names: {', '.join(PROCESS_NAMES)}")
    print(f"Collecting data every {MONITOR_INTERVAL_SECONDS} seconds for {MONITOR_DURATION_MINUTES} minutes.")

    # Initialize CSV file
    with open(OUTPUT_CSV_FILE, 'w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['Timestamp', 'Total CPU (%)', 'Total Memory (%)'])

    start_time = time.time()
    end_time = start_time + (MONITOR_DURATION_MINUTES * 60)

    # Prime CPU usage for accurate first reading (psutil.cpu_percent requires a second call)
    psutil.cpu_percent(interval=0.1) 

    while time.time() < end_time:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cpu, memory = get_process_info(PROCESS_NAMES)
        
        # Write data to CSV
        with open(OUTPUT_CSV_FILE, 'a', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow([timestamp, f"{cpu:.2f}", f"{memory:.2f}"])
        
        print(f"[{timestamp}] CPU: {cpu:.2f}% | Memory: {memory:.2f}%")
        time.sleep(MONITOR_INTERVAL_SECONDS)

    print(f"Monitoring complete. Data saved to '{OUTPUT_CSV_FILE}'")

if __name__ == '__main__':
    # You might need to adjust PROCESS_NAMES based on how you run your app.
    # For example, if your PyInstaller exe is named 'my_streamer.exe', change:
    # PROCESS_NAMES = ["my_streamer.exe", "ffmpeg.exe"]
    
    # If running directly from python:
    # PROCESS_NAMES = ["python.exe", "ffmpeg.exe"] 
    
    # If you want to include the main app's python process, ensure 'python.exe' or 'pythonw.exe' is listed.
    # The current list covers common scenarios.

    monitor_resources()
