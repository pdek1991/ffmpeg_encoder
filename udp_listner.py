import socket

# --- Configuration ---
# IMPORTANT: This IP and Port MUST match the "Output IP Address" and "Output Port"
# configured in your FFmpeg Streamer application.
# If your FFmpeg output is a multicast address (e.g., 239.x.x.x),
# this listener should bind to '0.0.0.0' or the specific local interface IP,
# and join the multicast group.
# If it's a unicast address (e.g., 127.0.0.1 or your machine's IP),
# bind to that specific IP or '0.0.0.0'.

LISTEN_IP = "0.0.0.0" # Use '0.0.0.0' to listen on all available interfaces
LISTEN_PORT = 5678   # This should match your FFmpeg Streamer's output port
MULTICAST_GROUP = "239.2.2.6" # Set this if your FFmpeg output is a multicast IP, otherwise leave as None or comment out

# --- Listener Logic ---
def udp_listener(ip, port, multicast_group=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    
    # Allow reuse of address and port
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'): # SO_REUSEPORT is not available on all OS (e.g., Windows)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    # Bind to the specified IP and Port
    try:
        sock.bind((ip, port))
        print(f"UDP Listener started on {ip}:{port}")
    except OSError as e:
        print(f"Error binding socket to {ip}:{port}: {e}")
        print("Please ensure the port is not already in use by another application.")
        print("If using a specific IP, ensure it's configured on your machine's network interface.")
        return

    # If it's a multicast address, join the multicast group
    if multicast_group:
        try:
            # For Windows, you might need to specify the interface IP for joining multicast
            # For example: mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton("YOUR_LOCAL_INTERFACE_IP")
            # For simplicity, we'll try the general approach first.
            mreq = socket.inet_aton(multicast_group) + socket.inet_aton(ip) # Use LISTEN_IP for interface if 0.0.0.0
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            print(f"Joined multicast group: {multicast_group}")
        except Exception as e:
            print(f"Error joining multicast group {multicast_group}: {e}")
            print("Ensure your network supports multicast and the IP is a valid multicast address.")
            # Continue listening even if multicast join fails, as it might still receive unicast.

    print("Waiting for UDP packets...")
    try:
        while True:
            data, addr = sock.recvfrom(65536) # Buffer size 65536 bytes (max UDP packet size)
            print(f"Received {len(data)} bytes from {addr}")
            # You can uncomment the line below to see a small part of the data
            # print(f"Data snippet: {data[:50]}...")
    except KeyboardInterrupt:
        print("\nListener stopped by user.")
    except Exception as e:
        print(f"An error occurred during listening: {e}")
    finally:
        sock.close()
        print("Socket closed.")

if __name__ == "__main__":
    # Example usage:
    # If your FFmpeg output is unicast to 127.0.0.1:5678:
    # udp_listener("127.0.0.1", 5678)

    # If your FFmpeg output is multicast to 239.2.2.2:5678:
    # You MUST set MULTICAST_GROUP correctly above.
    # The LISTEN_IP should be '0.0.0.0' or your local interface IP.
    udp_listener(LISTEN_IP, LISTEN_PORT, MULTICAST_GROUP)

    # If your FFmpeg output is unicast to your specific local IP (e.g., 192.168.1.100:5678)
    # udp_listener("192.168.1.100", 5678)
