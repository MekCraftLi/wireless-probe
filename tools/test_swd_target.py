#!/usr/bin/env python3
"""
Test SWD connection to target chip over Wi-Fi 6 wireless bridge.
Performs SWD synchronization and reads ARM CoreSight DPIDR register.
Supports STM32H7 (0x6BA02477), STM32F1/F4 (0x1BA01477), RP2040 (0x0BC12477), RP2350 (0x4C013477), etc.
"""

import socket
import struct
import sys
import time

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240

def xfer(s, req, name=""):
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

    print("Connected. Initiating SWD handshake...")

    # 1. DAP_Connect (Port SWD = 1)
    r = xfer(s, [0x02, 0x01], "DAP_Connect")
    if len(r) < 2 or r[1] != 1:
        print(f"Failed to connect to SWD port! Response: {list(r)}")
        s.close()
        return 1
    print("  [OK] SWD Port Connected.")

    # 2. DAP_SWJ_Clock (500 kHz = 0x0007A120)
    xfer(s, [0x11, 0x20, 0xA1, 0x07, 0x00], "DAP_SWJ_Clock")
    print("  [OK] SWD Clock set to 500 kHz.")

    # 3. DAP_TransferConfigure (idle=0, retry=50, match_retry=0)
    xfer(s, [0x04, 0x00, 0x32, 0x00, 0x00, 0x00], "DAP_TransferConfigure")

    # 4. ARM Standard SWD Line Reset + JTAG-to-SWD Switch Sequence
    xfer(s, [0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], "LineReset 1")
    xfer(s, [0x12, 16, 0x9E, 0xE7], "JTAG-to-SWD Switch (0xE79E)")
    xfer(s, [0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], "LineReset 2")
    xfer(s, [0x12, 8, 0x00], "Idle Clocks")
    print("  [OK] SWD Synchronization sequence executed.")

    # 5. Read DPIDR (DP register 0)
    # DAP_Transfer: dap_index=0, count=1, request=0x02 (DP Read, addr 0x00)
    r = xfer(s, [0x05, 0x00, 0x01, 0x02], "Read DPIDR")
    if len(r) >= 7 and r[1] == 1 and (r[2] & 0x07) == 1:
        dpidr = struct.unpack("<I", r[3:7])[0]
        print(f"\n=======================================================")
        print(f"  TARGET DETECTED! DPIDR = 0x{dpidr:08X}")
        print(f"=======================================================")

        chip_map = {
            0x6BA02477: "ARM Cortex-M7 DPv2 (e.g. STM32H7 / i.MX RT)",
            0x5BA02477: "ARM Cortex-M4/M33 DPv2 (e.g. STM32U5 / STM32H5)",
            0x2BA01477: "ARM Cortex-M4 DPv1 (e.g. STM32F4 / STM32F3)",
            0x1BA01477: "ARM Cortex-M3 DPv1 (e.g. STM32F103 / STM32F2)",
            0x0BC12477: "ARM Cortex-M0+ (e.g. RP2040 / STM32G0 / SAMD21)",
            0x0BB11477: "ARM Cortex-M0+ (e.g. STM32L0)",
            0x4C013477: "Raspberry Pi RP2350 (Cortex-M33 / Hazard3 DPv3)",
        }
        name = chip_map.get(dpidr, "Unknown ARM CoreSight DP")
        print(f"  Target Type: {name}\n")

        # 6. Power-up debug subsystem
        print("Powering up CoreSight debug subsystem...")
        # Write DP_CTRL_STAT (0x50000000 = CSYSPWRUPREQ | CDBGPWRUPREQ)
        xfer(s, [0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50], "Power Up Request")
        r = xfer(s, [0x05, 0x00, 0x01, 0x06], "Read CTRL/STAT")
        if len(r) >= 7:
            ctrl_stat = struct.unpack("<I", r[3:7])[0]
            print(f"  [OK] DP CTRL/STAT = 0x{ctrl_stat:08X}")

        # 7. Read CoreSight CPUID via MEM-AP (AP0)
        # Select AP0, Bank 0 (CSW/TAR)
        xfer(s, [0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00], "DP_SELECT AP0")
        # AP0 CSW = 0x23000052 (Size=32-bit, DbgStatus, AddrInc=Single)
        xfer(s, [0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23], "AP0_CSW")
        # AP0 TAR = 0xE000ED00 (SCB CPUID base address)
        xfer(s, [0x05, 0x00, 0x01, 0x05, 0x00, 0xED, 0x00, 0xE0], "AP0_TAR CPUID")
        # AP0 DRW dummy read (initiates bus cycle)
        xfer(s, [0x05, 0x00, 0x01, 0x0F], "AP0_DRW Read Post")
        # DP RDBUFF read actual captured data
        r = xfer(s, [0x05, 0x00, 0x01, 0x0E], "DP_RDBUFF")
        if len(r) >= 7 and r[1] == 1 and (r[2] & 0x07) == 1:
            cpuid = struct.unpack("<I", r[3:7])[0]
            part_no = (cpuid >> 4) & 0x0FFF
            part_names = {
                0xC27: "Cortex-M7 (STM32H7)",
                0xC24: "Cortex-M4 (STM32F4)",
                0xC23: "Cortex-M3 (STM32F1)",
                0xC60: "Cortex-M0+ (RP2040)",
                0xD21: "Cortex-M33 (RP2350)",
            }
            core_name = part_names.get(part_no, f"Part 0x{part_no:03X}")
            print(f"  [OK] ARM Core CPUID: 0x{cpuid:08X} -> {core_name}")
            print(f"  Implementer: 0x{(cpuid>>24)&0xFF:02X} (ARM), Variant: r{(cpuid>>20)&0xF}p{cpuid&0xF}")
    else:
        print(f"\n  [ERROR] Read DPIDR failed! Raw response: {list(r)}")
        print("  Check SWD wiring: Pin 4 (SWCLK), Pin 5 (SWDIO), and Pin 3 (GND Common)!")

    # 8. DAP_Disconnect
    xfer(s, [0x03], "DAP_Disconnect")
    s.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
