#!/usr/bin/env python3
"""
Local TCP Proxy / Bridge for Wireless CMSIS-DAP Probe.
Listens on local TCP port (default 127.0.0.1:3240) and forwards packets to the wireless probe (192.168.4.1:3240).
Allows seamless connection from OpenOCD, pyOCD, or IDEs running on the local host.
"""

import socket
import select
import sys

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 3240
PROBE_HOST = "192.168.4.1"
PROBE_PORT = 3240

def handle_client(client_sock, remote_host, remote_port):
    print(f"Connecting to remote wireless probe at {remote_host}:{remote_port}...")
    try:
        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        remote_sock.connect((remote_host, remote_port))
    except Exception as e:
        print(f"Failed to connect to remote probe: {e}")
        client_sock.close()
        return

    print("Tunnel established. Forwarding traffic...")
    sockets = [client_sock, remote_sock]

    try:
        while True:
            r, _, _ = select.select(sockets, [], [])
            for s in r:
                data = s.recv(4096)
                if not data:
                    return
                if s is client_sock:
                    remote_sock.sendall(data)
                else:
                    client_sock.sendall(data)
    except Exception as e:
        print(f"Tunnel error: {e}")
    finally:
        client_sock.close()
        remote_sock.close()
        print("Tunnel closed.")

def main():
    remote_ip = sys.argv[1] if len(sys.argv) > 1 else PROBE_HOST
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LOCAL_HOST, LOCAL_PORT))
    server.listen(1)
    print(f"TCP Bridge listening on {LOCAL_HOST}:{LOCAL_PORT} -> Forwarding to {remote_ip}:{PROBE_PORT}")

    try:
        while True:
            client, addr = server.accept()
            print(f"Accepted connection from {addr}")
            handle_client(client, remote_ip, PROBE_PORT)
    except KeyboardInterrupt:
        print("\nStopping bridge server.")
    finally:
        server.close()

if __name__ == "__main__":
    main()
