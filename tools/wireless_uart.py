#!/usr/bin/env python3
"""
Wireless UART Serial Monitor.
Connects to ESP32-C5 TCP port 3243 to receive and display target MCU printf logs in real-time.
"""

import socket
import sys
import threading

PROBE_IP = "192.168.4.1"
UART_PORT = 3243

def main():
    target_ip = sys.argv[1] if len(sys.argv) > 1 else PROBE_IP
    print(f"Connecting to Wireless UART at {target_ip}:{UART_PORT}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((target_ip, UART_PORT))
    except Exception as e:
        print(f"Connection failed: {e}")
        return 1

    print(f"Connected to Wireless UART! Listening for target logs... (Ctrl+C to exit)\n---")

    def stdin_sender():
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                s.sendall(line.encode("utf-8"))
        except Exception:
            pass

    t = threading.Thread(target=stdin_sender, daemon=True)
    t.start()

    try:
        while True:
            data = s.recv(1024)
            if not data:
                print("\n[Connection closed by server]")
                break
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        s.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
