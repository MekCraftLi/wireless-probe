#!/usr/bin/env python3
"""
Test reading memory and core registers (CPUID) via AHB-AP over Wi-Fi 6 wireless bridge.
"""

import socket
import struct
import sys
import time

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240

def xfer(s, req):
    s.sendall(bytes(req))
    resp = s.recv(1024)
    return resp

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

    # Connect SWD & Sync
    xfer(s, [0x02, 0x01])
    xfer(s, [0x11, 0x40, 0x42, 0x0F, 0x00]) # 1MHz
    xfer(s, [0x04, 0x00, 0x32, 0x00, 0x00, 0x00])
    xfer(s, [0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    xfer(s, [0x12, 16, 0x9E, 0xE7])
    xfer(s, [0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    xfer(s, [0x12, 8, 0x00])

    # Read DPIDR
    r = xfer(s, [0x05, 0x00, 0x01, 0x02])
    if len(r) < 7 or r[1] != 1 or (r[2] & 0x07) != 1:
        print("Failed to read DPIDR!")
        s.close()
        return 1

    dpidr = struct.unpack("<I", r[3:7])[0]
    print(f"Target DPIDR: 0x{dpidr:08X}")

    # Power up debug & system domains: Write DP CTRL/STAT (addr 0x04) with CSYSPWRUPREQ | CDBGPWRUPREQ (0x50000000)
    xfer(s, [0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
    time.sleep(0.01)

    # Select AP 0 (AHB-AP): Write DP SELECT (addr 0x08) with 0x00000000
    xfer(s, [0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])

    # Configure AP CSW: 0x23000052 (32-bit transfer, auto increment)
    xfer(s, [0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

    # Set TAR = 0xE000ED00 (CPUID)
    xfer(s, [0x05, 0x00, 0x01, 0x05, 0x00, 0xED, 0x00, 0xE0])

    # Read DRW (initiates transfer) then Read RDBUFF
    r = xfer(s, [0x05, 0x00, 0x02, 0x0F, 0x0E])
    if len(r) >= 7 and (r[2] & 0x07) == 1:
        cpuid = struct.unpack("<I", r[3:7])[0]
        implementer = (cpuid >> 24) & 0xFF
        variant = (cpuid >> 20) & 0x0F
        partno = (cpuid >> 4) & 0x0FFF
        revision = cpuid & 0x0F

        cores = {
            0xC20: "Cortex-M0",
            0xC60: "Cortex-M0+",
            0xC21: "Cortex-M1",
            0xC23: "Cortex-M3",
            0xC24: "Cortex-M4",
            0xC27: "Cortex-M7",
            0xD20: "Cortex-M23",
            0xD21: "Cortex-M33",
            0xD22: "Cortex-M55",
            0xD23: "Cortex-M85",
        }
        core_name = cores.get(partno, f"Part 0x{partno:03X}")

        print(f"\n=======================================================")
        print(f"  MEMORY BUS ACCESS SUCCESSFUL!")
        print(f"  CPUID Register (0xE000ED00): 0x{cpuid:08X}")
        print(f"  Architecture: ARM (0x{implementer:02X})")
        print(f"  CPU Core:     {core_name}")
        print(f"  Core Rev:     r{variant}p{revision}")
        print(f"=======================================================\n")
    else:
        print(f"Memory read failed: response {list(r)}")

    xfer(s, [0x03])
    s.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
