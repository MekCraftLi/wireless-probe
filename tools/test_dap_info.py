#!/usr/bin/env python3
"""
Test DAP_Info command over Wi-Fi 6 wireless bridge.
Queries CMSIS-DAP firmware version, vendor name, capabilities.
"""

import socket
import sys
import time

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240

def main():
    target_ip = sys.argv[1] if len(sys.argv) > 1 else PROBE_IP
    print(f"Connecting to wireless probe at {target_ip}:{PROBE_PORT}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        s.connect((target_ip, PROBE_PORT))
    except Exception as e:
        print(f"Connection failed: {e}")
        return 1

    print("Connected successfully!\n")

    info_queries = [
        (0x01, "Vendor Name"),
        (0x02, "Product Name"),
        (0x03, "Serial Number"),
        (0x04, "Firmware Version"),
        (0xF0, "Capabilities"),
    ]

    for info_id, desc in info_queries:
        req = bytes([0x00, info_id])
        s.sendall(req)
        try:
            resp = s.recv(1024)
            if len(resp) >= 2 and resp[0] == 0x00:
                length = resp[1]
                val = resp[2:2 + length]
                if info_id == 0xF0:
                    caps = val[0] if len(val) > 0 else 0
                    swd_supported = bool(caps & 1)
                    jtag_supported = bool(caps & 2)
                    print(f"[{desc:16s}] Caps byte: 0x{caps:02X} (SWD: {swd_supported}, JTAG: {jtag_supported})")
                else:
                    str_val = val.decode("utf-8", errors="replace").strip("\x00")
                    print(f"[{desc:16s}] '{str_val}' (raw: {list(resp)})")
            else:
                print(f"[{desc:16s}] Raw response: {list(resp)}")
        except socket.timeout:
            print(f"[{desc:16s}] Request timed out!")
        time.sleep(0.05)

    s.close()
    print("\nDAP_Info test completed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
