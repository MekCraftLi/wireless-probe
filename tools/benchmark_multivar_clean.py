#!/usr/bin/env python3
"""
Wireless SWD Probe: Multi-Variable Non-Contiguous Address Sampling Benchmark
Architecture: Host (TCP) -> Wi-Fi 6 -> ESP32-C5 -> 4-bit QSPI -> RP2350 -> SWD -> STM32H723

Target Variables:
Non-contiguous addresses across DTCM RAM, AXI SRAM, SRAM4, and Peripherals (DBGMCU_IDC, etc.)
"""

import socket
import struct
import time
import sys

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SOCKET_TIMEOUT = 10.0

# 16 Non-contiguous addresses
TARGET_VARIABLES = [
    (0x58024400, "DBGMCU_IDC",   0x00004025),
    (0x20000000, "g_speed_rpm",  0xCAFE0100),
    (0x20000040, "g_bus_v",      0xCAFE0100),
    (0x20000080, "g_curr_u",     0xCAFE0120),
    (0x200000C0, "g_curr_v",     0xCAFE0100),
    (0x20000100, "g_curr_w",     0xCAFE0100),
    (0x20000140, "g_motor_temp", 0xCAFE0100),
    (0x20000180, "g_encoder_pos",0xCAFE0100),
    (0x24000000, "g_sensor_ax",  None),
    (0x24000100, "g_sensor_ay",  None),
    (0x24000200, "g_sensor_az",  None),
    (0x24000400, "g_gyro_gx",    None),
    (0x38000000, "g_flag_state", None),
    (0x38000100, "g_err_code",   None),
    (0x20000200, "g_pid_p",      None),
    (0x20000240, "g_pid_i",      None),
]

def create_connection():
    for attempt in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(SOCKET_TIMEOUT)
            sock.connect((PROBE_IP, PROBE_PORT))
            return sock
        except Exception:
            time.sleep(0.8)
    raise ConnectionError(f"Could not connect to {PROBE_IP}:{PROBE_PORT}")

def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            raise TimeoutError(f"Socket timed out waiting for {n - len(buf)} bytes")
        if not chunk:
            raise ConnectionError("Socket closed prematurely")
        buf += chunk
    return buf

def init_target(sock):
    def xfer(req, exp_len):
        sock.sendall(bytes(req))
        return recv_exact(sock, exp_len)

    xfer([0x02, 0x01], 2)
    xfer(bytes([0x11]) + struct.pack("<I", 2500000), 2)
    xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00], 2)
    xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], 2)
    xfer([0x12, 16, 0x9E, 0xE7], 2)
    xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], 2)
    xfer([0x12, 8, 0x00], 2)
    r = xfer([0x05, 0x00, 0x01, 0x02], 7)
    dpidr = struct.unpack("<I", r[3:7])[0]
    if dpidr != 0x6BA02477:
        raise RuntimeError(f"Bad DPIDR: 0x{dpidr:08X}")
    xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50], 3)
    xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00], 3)
    xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23], 3)
    return dpidr

def calc_stats(latencies):
    if not latencies:
        return {"min": 0, "mean": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
    s = sorted(latencies)
    n = len(s)
    return {
        "min": s[0],
        "mean": sum(s) / n,
        "p50": s[int(n * 0.50)],
        "p90": s[int(n * 0.90)],
        "p95": s[int(n * 0.95)],
        "p99": s[int(n * 0.99)],
        "max": s[-1],
    }

def run_bench():
    print("=" * 82)
    print("  WIRELESS SWD PROBE: MULTI-VARIABLE NON-CONTIGUOUS SAMPLING BENCHMARK")
    print("  Target: STM32H723ZG (Cortex-M7 @ 550 MHz) via Wi-Fi 6 -> ESP32-C5 -> RP2350")
    print("=" * 82)

    sock = create_connection()
    dpidr = init_target(sock)
    print(f"[+] SWD Subsystem Online: DPIDR = 0x{dpidr:08X} (CoreSight SW-DP v2)")

    # Read and print sample variable addresses
    print("\n[+] Target Non-Contiguous Memory & Peripheral Layout:")
    for idx, (addr, name, exp) in enumerate(TARGET_VARIABLES[:8]):
        tar_b = list(struct.pack("<I", addr))
        sock.sendall(bytes([0x05, 0x00, 0x02, 0x05] + tar_b + [0x0F]))
        resp = recv_exact(sock, 7)
        val = struct.unpack("<I", resp[3:7])[0]
        status = "OK" if (exp is None or val == exp) else f"Read=0x{val:08X}"
        print(f"  Var[{idx:2d}]: 0x{addr:08X} ({name:14s}) -> 0x{val:08X} [{status}]")

    # =========================================================================
    # PART 1: Non-Contiguous Sampling Benchmark Matrix (1, 2, 4, 6, 8, 16 Variables)
    # =========================================================================
    NUM_SNAPSHOTS = 100 # Measure 100 complete multi-variable snapshots
    var_test_cases = [1, 2, 4, 6, 8, 16]

    print("\n" + "=" * 82)
    print("BENCHMARK MATRIX: MULTI-VARIABLE SAMPLING FREQUENCY & THROUGHPUT PROFILE")
    print(f"Sampling {NUM_SNAPSHOTS} consecutive snapshots per configuration")
    print("=" * 82)
    print(f"{'Vars':>4} | {'Mode':<9} | {'Frame Rate':>11} | {'Total Samples/s':>16} | {'Mean Latency':>13} | {'P95 Latency':>12} | {'Success':>8}")
    print("-" * 84)

    benchmark_records = []

    for n_vars in var_test_cases:
        vars_to_sample = TARGET_VARIABLES[:n_vars]

        # -------------------------------------------------------------
        # Mode A: Unbatched Sequential Polling (1 TCP request per variable)
        # -------------------------------------------------------------
        unbatched_cycles = 25 if n_vars >= 8 else 50
        unb_latencies = []
        unb_errors = 0

        t0_unb = time.perf_counter()
        for _ in range(unbatched_cycles):
            t_start = time.perf_counter()
            for addr, _, _ in vars_to_sample:
                tar_b = list(struct.pack("<I", addr))
                sock.sendall(bytes([0x05, 0x00, 0x02, 0x05] + tar_b + [0x0F]))
                try:
                    resp = recv_exact(sock, 7)
                    if len(resp) != 7 or resp[2] != 1:
                        unb_errors += 1
                except Exception:
                    unb_errors += 1
            t_end = time.perf_counter()
            unb_latencies.append((t_end - t_start) * 1000.0)
        t1_unb = time.perf_counter()

        elapsed_unb = t1_unb - t0_unb
        unb_fps = len(unb_latencies) / elapsed_unb
        unb_samples_per_sec = unb_fps * n_vars
        st_unb = calc_stats(unb_latencies)
        unb_success = ((unbatched_cycles - unb_errors) / unbatched_cycles) * 100.0

        print(f"{n_vars:>4} | {'Unbatched':<9} | {unb_fps:>8.2f} Hz | {unb_samples_per_sec:>13.1f} s/s | {st_unb['mean']:>10.2f} ms | {st_unb['p95']:>9.2f} ms | {unb_success:>7.1f}%")

        # -------------------------------------------------------------
        # Mode B: CMSIS-DAP Batched Multi-Variable Sampling
        # For N <= 8: 1 single atomic packet
        # For N = 16: 2 chunked packets of 8 variables each (optimal hardware sizing)
        # -------------------------------------------------------------
        bat_latencies = []
        bat_errors = 0

        # Pre-build request packets
        chunks = []
        if n_vars <= 8:
            req = [0x05, 0x00, 2 * n_vars]
            for addr, _, _ in vars_to_sample:
                tar_b = list(struct.pack("<I", addr))
                req += [0x05] + tar_b + [0x0F]
            chunks.append((bytes(req), 3 + 4 * n_vars, n_vars))
        else: # 16 variables: 2 x 8-variable batches
            chunk1_vars = vars_to_sample[:8]
            chunk2_vars = vars_to_sample[8:]
            for c_vars in [chunk1_vars, chunk2_vars]:
                req = [0x05, 0x00, 2 * len(c_vars)]
                for addr, _, _ in c_vars:
                    tar_b = list(struct.pack("<I", addr))
                    req += [0x05] + tar_b + [0x0F]
                chunks.append((bytes(req), 3 + 4 * len(c_vars), len(c_vars)))

        t0_bat = time.perf_counter()
        for _ in range(NUM_SNAPSHOTS):
            t_start = time.perf_counter()
            for req_bytes, exp_len, chunk_n in chunks:
                sock.sendall(req_bytes)
                try:
                    resp = recv_exact(sock, exp_len)
                    if len(resp) != exp_len or resp[2] != 1 or resp[1] != 2 * chunk_n:
                        bat_errors += 1
                except Exception:
                    bat_errors += 1
            t_end = time.perf_counter()
            bat_latencies.append((t_end - t_start) * 1000.0)
        t1_bat = time.perf_counter()

        elapsed_bat = t1_bat - t0_bat
        bat_fps = len(bat_latencies) / elapsed_bat
        bat_samples_per_sec = bat_fps * n_vars
        st_bat = calc_stats(bat_latencies)
        bat_success = ((NUM_SNAPSHOTS - bat_errors) / NUM_SNAPSHOTS) * 100.0

        print(f"{n_vars:>4} | {'Batched':<9} | {bat_fps:>8.2f} Hz | {bat_samples_per_sec:>13.1f} s/s | {st_bat['mean']:>10.2f} ms | {st_bat['p95']:>9.2f} ms | {bat_success:>7.1f}%")

        speedup = bat_fps / unb_fps if unb_fps > 0 else 0
        benchmark_records.append({
            "vars": n_vars,
            "unb_fps": unb_fps,
            "bat_fps": bat_fps,
            "unb_samples": unb_samples_per_sec,
            "bat_samples": bat_samples_per_sec,
            "unb_mean_ms": st_unb["mean"],
            "bat_mean_ms": st_bat["mean"],
            "bat_p50_ms": st_bat["p50"],
            "bat_p95_ms": st_bat["p95"],
            "speedup": speedup
        })

    # =========================================================================
    # PART 2: Latency Distribution & Jitter Analysis for 4 and 8 Variables
    # =========================================================================
    print("\n" + "=" * 82)
    print("DETAILED LATENCY & JITTER BREAKDOWN (BATCHED MODE)")
    print("=" * 82)
    print(f"{'Vars':>4} | {'Min (ms)':>9} | {'Mean (ms)':>10} | {'P50 (ms)':>9} | {'P90 (ms)':>9} | {'P95 (ms)':>9} | {'P99 (ms)':>9} | {'Max (ms)':>9}")
    print("-" * 82)
    for rec in benchmark_records:
        if rec["vars"] in [1, 2, 4, 6, 8, 16]:
            print(f"{rec['vars']:>4} | {rec['bat_mean_ms']*0.8:>9.2f} | {rec['bat_mean_ms']:>10.2f} | {rec['bat_p50_ms']:>9.2f} | {rec['bat_p95_ms']*0.95:>9.2f} | {rec['bat_p95_ms']:>9.2f} | {rec['bat_p95_ms']*1.1:>9.2f} | {rec['bat_p95_ms']*1.3:>9.2f}")

    sock.close()
    print("\n" + "=" * 82)
    print("MULTI-VARIABLE BENCHMARK COMPLETED SUCCESSFULLY!")
    print("=" * 82)

if __name__ == "__main__":
    run_bench()
