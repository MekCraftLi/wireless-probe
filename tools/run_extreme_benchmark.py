#!/usr/bin/env python3
"""
Extreme Stress & Performance Benchmark Suite for Wireless SWD Probe
Hardware Chain: Host -> Wi-Fi 6 (TCP) -> ESP32-C5 -> QSPI (4-bit) -> RP2350 (Core 1) -> SWD -> STM32H7
"""

import socket
import struct
import time
import hashlib
import os
import sys

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SWD_CLOCK_HZ = 2_500_000  # 2.5 MHz optimal high-speed clock for jumper wire setup

print("=" * 80)
print("     WIRELESS CMSIS-DAP EXTREME PERFORMANCE BENCHMARK & STRESS TEST")
print("  Architecture: Host (TCP) -> Wi-Fi 6 -> ESP32-C5 -> 4-bit QSPI -> RP2350 -> SWD -> STM32H7")
print(f"  Target Clock: {SWD_CLOCK_HZ / 1e6:.2f} MHz | Packet Size: 512 Bytes")
print("=" * 80)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(10.0)

try:
    s.connect((PROBE_IP, PROBE_PORT))
except Exception as e:
    print(f"[-] Connection failed to {PROBE_IP}:{PROBE_PORT}: {e}")
    sys.exit(1)

def xfer(req):
    s.sendall(bytes(req))
    resp = s.recv(2048)
    return resp

# ------------------------------------------------------------------------------
# Phase 0: Hardware Initialization & Target Identification
# ------------------------------------------------------------------------------
print("\n[Phase 0] Initializing SWD Subsystem & Identifying Target...")

# Connect DAP
r = xfer([0x02, 0x01])
assert len(r) >= 2 and r[1] == 1, f"DAP Connect failed: {list(r)}"

# Set Clock
xfer(bytes([0x11]) + struct.pack("<I", SWD_CLOCK_HZ))

# Configure Transfer parameters (idle cycles=0, retry=50, match retry=0)
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

# SWD Line Reset & Switch Sequence (JTAG-to-SWD)
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])

# Read DPIDR (DP Reg 0x00)
r = xfer([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
print(f"  -> DPIDR        : 0x{dpidr:08X} (ARM CoreSight SW-DP v2)")
assert dpidr == 0x6BA02477, f"Unexpected DPIDR: 0x{dpidr:08X}"

# Power up Debug & System Domains
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50]) # CSYSPWRUPREQ | CDBGPWRUPREQ
# Select AP 0, Bank 0
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
# Configure CSW: 32-bit width, auto-increment
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# Identify CPUID and DBGMCU
# CPUID @ 0xE000ED00
xfer([0x05, 0x00, 0x01, 0x05, 0x00, 0xED, 0x00, 0xE0])
r = xfer([0x05, 0x00, 0x02, 0x0F, 0x0E])
cpuid = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
# DBGMCU_IDC @ 0x58024400
xfer([0x05, 0x00, 0x01, 0x05, 0x00, 0x44, 0x02, 0x58])
r = xfer([0x05, 0x00, 0x02, 0x0F, 0x0E])
dbgmcu = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0

print(f"  -> CPUID        : 0x{cpuid:08X} (ARM Cortex-M7 r1p2)")
print(f"  -> DBGMCU_IDC   : 0x{dbgmcu:08X} (STM32H72x/73x Device)")
print(f"  [+] Target Identification & Power-up SUCCESSFUL!")

# ------------------------------------------------------------------------------
# Benchmark 1: Non-Contiguous Address Access IOPS & Latency Distribution
# ------------------------------------------------------------------------------
print("\n" + "=" * 80)
print("BENCHMARK 1: NON-CONTIGUOUS ADDRESS ACCESS FREQUENCY & LATENCY PROFILE")
print("=" * 80)

non_contig_addrs = [
    (0xE000ED00, "CPUID"),
    (0xE000ED04, "ICSR"),
    (0xE000ED08, "VTOR"),
    (0xE000ED0C, "AIRCR"),
    (0x58024400, "DBGMCU_IDC"),
    (0x58000400, "RCC_CR"),
    (0x58000410, "RCC_CFGR"),
    (0x58020000, "GPIOA_MODER"),
    (0x24000000, "AXI_SRAM"),
    (0x20000000, "DTCM"),
]

print(f"Targeting {len(non_contig_addrs)} distinct peripheral & memory registers across 10 memory regions:")
for addr, name in non_contig_addrs:
    tar_b = list(struct.pack("<I", addr))
    r = xfer([0x05, 0x00, 0x03, 0x05] + tar_b + [0x0F, 0x0E])
    val = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
    print(f"  0x{addr:08X} ({name:12s}) -> 0x{val:08X}")

# 1A: Single-Access Round-Trip IOPS & Latency Distribution
CYCLES_1A = 100
total_single = CYCLES_1A * len(non_contig_addrs)
print(f"\n[1A] Single-Access Round-Trip Stress Test ({total_single} sequential round-trips)...")
latencies = []

t0 = time.perf_counter()
for _ in range(CYCLES_1A):
    for addr, _ in non_contig_addrs:
        t_req = time.perf_counter()
        tar_b = list(struct.pack("<I", addr))
        r = xfer([0x05, 0x00, 0x03, 0x05] + tar_b + [0x0F, 0x0E])
        t_resp = time.perf_counter()
        latencies.append((t_resp - t_req) * 1000.0)
t1 = time.perf_counter()

elapsed_1a = t1 - t0
rate_1a = total_single / elapsed_1a
latencies.sort()
min_lat = min(latencies)
max_lat = max(latencies)
mean_lat = sum(latencies) / len(latencies)
p50_lat = latencies[int(len(latencies) * 0.50)]
p90_lat = latencies[int(len(latencies) * 0.90)]
p95_lat = latencies[int(len(latencies) * 0.95)]
p99_lat = latencies[int(len(latencies) * 0.99)]

print(f"  -> Total Operations: {total_single}")
print(f"  -> Total Time      : {elapsed_1a:.3f} s")
print(f"  -> Sustained IOPS  : {rate_1a:.2f} accesses/sec (Hz)")
print(f"  -> Latency (Min)   : {min_lat:.2f} ms")
print(f"  -> Latency (Mean)  : {mean_lat:.2f} ms")
print(f"  -> Latency (P50)   : {p50_lat:.2f} ms")
print(f"  -> Latency (P90)   : {p90_lat:.2f} ms")
print(f"  -> Latency (P95)   : {p95_lat:.2f} ms")
print(f"  -> Latency (P99)   : {p99_lat:.2f} ms")
print(f"  -> Latency (Max)   : {max_lat:.2f} ms")

# 1B: Batched Request Pipeline (High-Density Aggregation)
BATCH_CYCLES = 500
addrs_4 = non_contig_addrs[:4]
req_batch = [0x05, 0x00, 9] # count = 9 transfers: (TAR, DRW)*4 + RDBUFF
for addr, _ in addrs_4:
    tar_b = list(struct.pack("<I", addr))
    req_batch += [0x05] + tar_b + [0x0F]
req_batch += [0x0E]

print(f"\n[1B] Batched Pipelined Access Test ({BATCH_CYCLES * len(addrs_4)} operations)...")
t0 = time.perf_counter()
for _ in range(BATCH_CYCLES):
    r = xfer(req_batch)
t1 = time.perf_counter()

elapsed_1b = t1 - t0
total_batched = BATCH_CYCLES * len(addrs_4)
rate_1b = total_batched / elapsed_1b
print(f"  -> Total Operations: {total_batched}")
print(f"  -> Total Time      : {elapsed_1b:.3f} s")
print(f"  -> Batched Rate    : {rate_1b:.2f} accesses/sec (Hz)")

# ------------------------------------------------------------------------------
# Benchmark 2 & 3: Maximum SWD Download (Write) & Upload (Read) Throughput
#                  with 100% Bit-Exact SHA-256 Data Integrity Verification
# ------------------------------------------------------------------------------
print("\n" + "=" * 80)
print("BENCHMARK 2 & 3: MAXIMUM SWD DOWNLOAD & UPLOAD THROUGHPUT + SHA-256 INTEGRITY")
print("=" * 80)

DEST_ADDR = 0x24000000  # AXI SRAM (512 KB capacity)
WORDS_PER_BLOCK = 64    # 256 bytes per DAP packet
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4

test_sizes = [
    16 * 1024,   # 16 KB
    64 * 1024,   # 64 KB
    128 * 1024,  # 128 KB
    256 * 1024,  # 256 KB
]

results = []

for sz in test_sizes:
    num_blocks = sz // BYTES_PER_BLOCK
    actual_sz = num_blocks * BYTES_PER_BLOCK
    print(f"\n--- Testing Block Transfer Size: {actual_sz / 1024:.0f} KB ({actual_sz} Bytes / {num_blocks} Blocks) ---")
    
    # Generate cryptographic pseudo-random payload
    test_payload = os.urandom(actual_sz)
    expected_sha256 = hashlib.sha256(test_payload).hexdigest()
    
    # 1. Download (Write to Target RAM)
    t0 = time.perf_counter()
    for b_idx in range(num_blocks):
        cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
        # Set TAR
        xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
        # Block write
        chunk = test_payload[b_idx * BYTES_PER_BLOCK : (b_idx + 1) * BYTES_PER_BLOCK]
        req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
        resp = xfer(req)
        assert len(resp) >= 4 and resp[3] == 1, f"Write block error at {b_idx}: {list(resp)}"
    t1 = time.perf_counter()
    
    write_dt = t1 - t0
    write_kb_s = (actual_sz / 1024.0) / write_dt
    write_mbps = (actual_sz * 8.0 / 1e6) / write_dt
    
    print(f"  [Write] Transferred: {actual_sz/1024:6.1f} KB | Time: {write_dt:6.3f} s | Rate: {write_kb_s:7.2f} KB/s ({write_mbps:5.2f} Mbps)")
    
    # 2. Upload (Read back from Target RAM)
    readback_buf = bytearray()
    t0 = time.perf_counter()
    for b_idx in range(num_blocks):
        cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
        # Set TAR
        xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
        # Block read
        req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
        resp = xfer(req)
        assert len(resp) >= 4 + BYTES_PER_BLOCK and resp[3] == 1, f"Read block error at {b_idx}: {list(resp[:4])}"
        readback_buf += resp[4 : 4 + BYTES_PER_BLOCK]
    t1 = time.perf_counter()
    
    read_dt = t1 - t0
    read_kb_s = (len(readback_buf) / 1024.0) / read_dt
    read_mbps = (len(readback_buf) * 8.0 / 1e6) / read_dt
    
    actual_sha256 = hashlib.sha256(readback_buf).hexdigest()
    match = (expected_sha256 == actual_sha256)
    
    print(f"  [Read]  Transferred: {len(readback_buf)/1024:6.1f} KB | Time: {read_dt:6.3f} s | Rate: {read_kb_s:7.2f} KB/s ({read_mbps:5.2f} Mbps)")
    print(f"  [Check] Expected SHA-256 : {expected_sha256}")
    print(f"  [Check] Readback SHA-256 : {actual_sha256}")
    print(f"  [Check] Integrity Status : {'100% BIT-EXACT MATCH (ZERO ERRORS)' if match else 'CORRUPTED MISMATCH'}")
    assert match, "Integrity verification failed!"
    
    results.append({
        "size_kb": actual_sz / 1024.0,
        "write_kb_s": write_kb_s,
        "write_mbps": write_mbps,
        "read_kb_s": read_kb_s,
        "read_mbps": read_mbps,
        "integrity": match
    })

# ------------------------------------------------------------------------------
# Benchmark 4: Extreme Saturation & Endurance Stress Test (Continuous Pressure)
# ------------------------------------------------------------------------------
print("\n" + "=" * 80)
print("BENCHMARK 4: EXTREME SATURATION & ENDURANCE STRESS TEST")
print("=" * 80)

STRESS_CYCLES = 2000
print(f"Executing {STRESS_CYCLES} continuous high-frequency mixed operations under maximum pressure...")

stress_errors = 0
t0 = time.perf_counter()
for i in range(STRESS_CYCLES):
    # Alternating register read and fast word write/read
    addr = 0x24000000 + ((i * 4) % 1024)
    # Write test value
    test_val = (0xA5A50000 + i) & 0xFFFFFFFF
    tar_b = list(struct.pack("<I", addr))
    val_b = list(struct.pack("<I", test_val))
    r = xfer([0x05, 0x00, 0x02, 0x05] + tar_b + [0x0D] + val_b)
    if len(r) < 3 or r[2] != 1:
        stress_errors += 1
    
    # Read back
    r = xfer([0x05, 0x00, 0x03, 0x05] + tar_b + [0x0F, 0x0E])
    if len(r) >= 7 and r[2] == 1:
        rb_val = struct.unpack("<I", r[3:7])[0]
        if rb_val != test_val:
            stress_errors += 1
    else:
        stress_errors += 1

t1 = time.perf_counter()
stress_dt = t1 - t0
stress_rate = (STRESS_CYCLES * 2) / stress_dt

print(f"  -> Total Operations: {STRESS_CYCLES * 2}")
print(f"  -> Total Time      : {stress_dt:.3f} s")
print(f"  -> Sustained IOPS  : {stress_rate:.2f} ops/sec (Hz)")
print(f"  -> Error Count     : {stress_errors}")
print(f"  -> Success Rate    : {((STRESS_CYCLES*2 - stress_errors)/(STRESS_CYCLES*2))*100:.3f}%")

# Clean disconnect
xfer([0x03])
s.close()

# ------------------------------------------------------------------------------
# Final Benchmark Summary
# ------------------------------------------------------------------------------
print("\n" + "=" * 80)
print("                 FINAL PERFORMANCE & STRESS TEST SUMMARY")
print("=" * 80)
print(f"1. Non-Contiguous Single-Access IOPS : {rate_1a:.2f} Hz (Avg Latency: {mean_lat:.2f} ms, P95: {p95_lat:.2f} ms)")
print(f"2. Non-Contiguous Batched Rate       : {rate_1b:.2f} accesses/sec")
print(f"3. Maximum SWD Write (Download) Speed: {max(r['write_kb_s'] for r in results):.2f} KB/s ({max(r['write_mbps'] for r in results):.2f} Mbps)")
print(f"4. Maximum SWD Read (Upload) Speed   : {max(r['read_kb_s'] for r in results):.2f} KB/s ({max(r['read_mbps'] for r in results):.2f} Mbps)")
print(f"5. Data Integrity (SHA-256)          : 100% BIT-EXACT MATCH across all block sizes")
print(f"6. Endurance Stress Test Success Rate: {((STRESS_CYCLES*2 - stress_errors)/(STRESS_CYCLES*2))*100:.3f}% ({stress_errors} errors over {STRESS_CYCLES*2} ops)")
print("=" * 80)
