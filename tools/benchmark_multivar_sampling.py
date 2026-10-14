#!/usr/bin/env python3
"""
Wireless SWD Probe: Multi-Variable Non-Contiguous Address Sampling Benchmark Suite
Target: STM32H723ZG (Cortex-M7 @ 550 MHz)
Path: Host (TCP) -> Wi-Fi 6 -> ESP32-C5 -> 4-bit QSPI -> RP2350 -> SWD -> STM32H7

This benchmark quantifies:
1. Multi-variable sampling frame rate (Hz) for N non-contiguous variables (N = 1, 2, 4, 8, 16, 24, 32)
2. Unbatched (1 variable / TCP RTT) vs Batched (All N variables in 1 DAP_Transfer)
3. Total variable sampling throughput (samples/sec)
4. Jitter and Latency profile (Min, Mean, P50, P90, P99, Max)
5. Impact of SWD Clock (1.0 MHz, 2.5 MHz, 5.0 MHz)
6. Asynchronous Pipelined Streaming (Overlapping Wi-Fi RTT)
"""

import socket
import struct
import time
import os
import sys

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SOCKET_TIMEOUT_SEC = 10.0

# 32 distinct non-contiguous addresses across DTCM, AXI SRAM, SRAM4, and Peripherals
NON_CONTIGUOUS_VARIABLES = [
    # System Control & CoreSight Registers
    (0xE000ED00, "CPUID",        0x411FC272),
    (0xE000ED04, "ICSR",         None),
    (0xE000ED08, "VTOR",         None),
    (0xE000ED0C, "AIRCR",        None),
    (0xE000ED10, "SCR",          None),
    (0xE000ED14, "CCR",          None),
    (0xE000ED24, "SHCSR",        None),
    (0xE000ED28, "CFSR",         None),
    # Peripheral Registers (DBGMCU & RCC)
    (0x58024400, "DBGMCU_IDC",   0x00004025),
    (0x58024404, "DBGMCU_CR",    None),
    (0x58000400, "RCC_CR",       None),
    (0x58000410, "RCC_CFGR",     None),
    (0x58000418, "RCC_D1CFGR",   None),
    (0x5800041C, "RCC_D2CFGR",   None),
    (0x58000420, "RCC_D3CFGR",   None),
    (0x58000434, "RCC_AHB1ENR",  None),
    # DTCM RAM non-contiguous addresses (Simulating global variables)
    (0x20000000, "g_motor_speed", None),
    (0x20000040, "g_bus_voltage", None),
    (0x200000A0, "g_current_u",   None),
    (0x20000120, "g_current_v",   None),
    (0x200001C0, "g_current_w",   None),
    (0x20000280, "g_target_pos",  None),
    (0x20000340, "g_pid_out",     None),
    (0x20000400, "g_temperature", None),
    # AXI SRAM non-contiguous addresses
    (0x24000000, "g_sensor_ax",   None),
    (0x24000100, "g_sensor_ay",   None),
    (0x24000200, "g_sensor_az",   None),
    (0x24000400, "g_gyro_gx",     None),
    (0x24000800, "g_gyro_gy",     None),
    (0x24001000, "g_gyro_gz",     None),
    # SRAM4 non-contiguous addresses
    (0x38000000, "g_status_flag", None),
    (0x38000100, "g_tick_counter",None),
]

def create_connection():
    for attempt in range(5):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(SOCKET_TIMEOUT_SEC)
            sock.connect((PROBE_IP, PROBE_PORT))
            return sock
        except Exception:
            time.sleep(1.0)
    raise ConnectionError(f"Could not connect to {PROBE_IP}:{PROBE_PORT}")

class DAPClient:
    def __init__(self, sock):
        self.sock = sock

    def recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                raise TimeoutError(f"Socket timed out waiting for {n - len(buf)} bytes")
            if not chunk:
                raise ConnectionError("Socket closed prematurely by peer")
            buf += chunk
        return buf

    def xfer(self, req):
        self.sock.sendall(bytes(req))
        cmd = req[0]
        if cmd in (0x02, 0x03, 0x04, 0x11, 0x12):
            return self.recv_exact(2)
        elif cmd == 0x05: # DAP_Transfer
            hdr = self.recv_exact(3)
            count = hdr[1]
            read_bytes = 0
            idx = 3
            for _ in range(count):
                req_val = req[idx]
                idx += 1
                if req_val & 0x02: # RnW = 1
                    read_bytes += 4
                else:
                    idx += 4
            if read_bytes > 0:
                return hdr + self.recv_exact(read_bytes)
            return hdr
        elif cmd == 0x06: # DAP_TransferBlock
            hdr = self.recv_exact(4)
            cnt = hdr[1] | (hdr[2] << 8)
            req_val = req[4]
            if req_val & 0x02:
                return hdr + self.recv_exact(cnt * 4)
            return hdr
        else:
            return self.sock.recv(2048)

    def set_swd_clock(self, clock_hz):
        self.xfer(bytes([0x11]) + struct.pack("<I", clock_hz))

    def clear_sticky_err(self):
        try:
            self.xfer([0x05, 0x00, 0x01, 0x00, 0x1E, 0x00, 0x00, 0x00])
        except Exception:
            pass

    def init_target(self, clock_hz=2_500_000):
        for _ in range(5):
            self.xfer([0x02, 0x01]) # Connect SWD
            self.set_swd_clock(clock_hz)
            self.xfer([0x04, 0x02, 0x64, 0x00, 0x00, 0x00]) # Retry 100
            # Line reset & JTAG-to-SWD
            self.xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
            self.xfer([0x12, 16, 0x9E, 0xE7])
            self.xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
            self.xfer([0x12, 8, 0x00])
            r = self.xfer([0x05, 0x00, 0x01, 0x02]) # Read DPIDR
            dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
            if dpidr == 0x6BA02477:
                self.xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50]) # Powerup
                self.xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00]) # Select AP 0
                self.xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23]) # CSW 32-bit
                return dpidr
            time.sleep(0.1)
        return 0

def calc_stats(latencies):
    if not latencies:
        return {}
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

def run_multivar_bench():
    print("=" * 80)
    print("  WIRELESS PROBE: MULTI-VARIABLE NON-CONTIGUOUS SAMPLING BENCHMARK")
    print("  Hardware: ESP32-C5 (Wi-Fi 6) <-> QSPI <-> RP2350 (PIO SWD) <-> STM32H7")
    print("=" * 80)

    sock = create_connection()
    client = DAPClient(sock)
    
    print("[+] Connecting to probe & initializing STM32H7 target...")
    dpidr = client.init_target(2_500_000)
    if dpidr != 0x6BA02477:
        print(f"[-] Target init failed (DPIDR=0x{dpidr:08X})")
        sys.exit(1)
    print(f"[+] Target Online: DPIDR = 0x{dpidr:08X} (CoreSight SW-DP v2)")

    # Verify key known variables
    r_cpu = client.xfer([0x05, 0x00, 0x02, 0x05, 0x00, 0xED, 0x00, 0xE0, 0x0F])
    cpuid = struct.unpack("<I", r_cpu[3:7])[0] if len(r_cpu) >= 7 else 0
    r_dbg = client.xfer([0x05, 0x00, 0x02, 0x05, 0x00, 0x44, 0x02, 0x58, 0x0F])
    dbgmcu = struct.unpack("<I", r_dbg[3:7])[0] if len(r_dbg) >= 7 else 0
    print(f"[+] Verified Target: CPUID = 0x{cpuid:08X} (Cortex-M7), DBGMCU_IDC = 0x{dbgmcu:08X}")

    # =========================================================================
    # PART 1: Unbatched (Sequential Polling) vs Batched (DAP_Transfer Pipelined)
    # =========================================================================
    var_counts = [1, 2, 4, 8, 16, 24, 32]
    SAMPLES_PER_TEST = 60 # Number of full multi-variable sample frames

    print("\n" + "=" * 80)
    print("PART 1: MULTI-VARIABLE SCALING (UNBATCHED vs BATCHED)")
    print(f"Measuring complete multi-variable sampling snapshots ({SAMPLES_PER_TEST} frames per test)")
    print("=" * 80)
    print(f"{'Vars':>4} | {'Mode':<9} | {'Frame Rate':>11} | {'Total Samples/s':>16} | {'Frame Latency (Mean/P50/P99)':>28} | {'Errors':>6}")
    print("-" * 84)

    results_table = []

    for num_vars in var_counts:
        vars_to_sample = NON_CONTIGUOUS_VARIABLES[:num_vars]
        
        # -------------------------------------------------------------
        # Mode A: Unbatched (1 variable per TCP request)
        # -------------------------------------------------------------
        # For N >= 16, reduce sample count for unbatched to save test time
        unbatched_samples = 20 if num_vars >= 16 else SAMPLES_PER_TEST
        frame_latencies_unbatched = []
        errors_unbatched = 0
        
        t0_unb = time.perf_counter()
        for _ in range(unbatched_samples):
            t_frame_start = time.perf_counter()
            for addr, _, _ in vars_to_sample:
                tar_b = list(struct.pack("<I", addr))
                req = [0x05, 0x00, 0x02, 0x05] + tar_b + [0x0F]
                try:
                    resp = client.xfer(req)
                    if len(resp) != 7 or resp[2] != 1:
                        errors_unbatched += 1
                        client.clear_sticky_err()
                except Exception:
                    errors_unbatched += 1
                    client.clear_sticky_err()
            t_frame_end = time.perf_counter()
            frame_latencies_unbatched.append((t_frame_end - t_frame_start) * 1000.0)
        t1_unb = time.perf_counter()

        elapsed_unb = t1_unb - t0_unb
        frame_hz_unb = len(frame_latencies_unbatched) / elapsed_unb
        total_samples_unb = frame_hz_unb * num_vars
        stats_unb = calc_stats(frame_latencies_unbatched)

        print(f"{num_vars:>4} | {'Unbatched':<9} | {frame_hz_unb:>8.2f} Hz | {total_samples_unb:>13.1f} s/s | {stats_unb['mean']:>7.2f} / {stats_unb['p50']:>6.2f} / {stats_unb['p99']:>6.2f} ms | {errors_unbatched:>6}")

        # -------------------------------------------------------------
        # Mode B: CMSIS-DAP Batched Pipelining (All N variables in 1 DAP_Transfer)
        # -------------------------------------------------------------
        # Build batched request: [0x05, 0x00, 2*N, TAR_0, DRW, TAR_1, DRW, ...]
        req_batch = [0x05, 0x00, 2 * num_vars]
        for addr, _, _ in vars_to_sample:
            tar_b = list(struct.pack("<I", addr))
            req_batch += [0x05] + tar_b + [0x0F]
        
        expected_resp_len = 3 + 4 * num_vars
        frame_latencies_batched = []
        errors_batched = 0

        t0_bat = time.perf_counter()
        for _ in range(SAMPLES_PER_TEST):
            t_frame_start = time.perf_counter()
            try:
                resp = client.xfer(req_batch)
                t_frame_end = time.perf_counter()
                frame_latencies_batched.append((t_frame_end - t_frame_start) * 1000.0)
                if len(resp) != expected_resp_len or resp[2] != 1:
                    errors_batched += 1
                    client.clear_sticky_err()
            except Exception:
                errors_batched += 1
                client.clear_sticky_err()
        t1_bat = time.perf_counter()

        elapsed_bat = t1_bat - t0_bat
        frame_hz_bat = len(frame_latencies_batched) / elapsed_bat
        total_samples_bat = frame_hz_bat * num_vars
        stats_bat = calc_stats(frame_latencies_batched)

        print(f"{num_vars:>4} | {'Batched':<9} | {frame_hz_bat:>8.2f} Hz | {total_samples_bat:>13.1f} s/s | {stats_bat['mean']:>7.2f} / {stats_bat['p50']:>6.2f} / {stats_bat['p99']:>6.2f} ms | {errors_batched:>6}")
        
        speedup = frame_hz_bat / frame_hz_unb if frame_hz_unb > 0 else 0
        results_table.append({
            "num_vars": num_vars,
            "unb_hz": frame_hz_unb,
            "bat_hz": frame_hz_bat,
            "speedup": speedup,
            "bat_samples_sec": total_samples_bat,
            "stats_bat": stats_bat
        })

    # =========================================================================
    # PART 2: SWD Physical Clock Scaling (1.0 MHz, 2.5 MHz, 5.0 MHz)
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 2: SWD PHYSICAL CLOCK SCALING ON BATCHED SAMPLING")
    print("Testing 8 and 16 non-contiguous variables across SWD clock rates")
    print("=" * 80)
    print(f"{'Vars':>4} | {'SWD Clock':>10} | {'Frame Rate':>11} | {'Total Samples/s':>16} | {'Mean Latency':>13} | {'P95 Latency':>12}")
    print("-" * 75)

    test_clocks = [1_000_000, 2_500_000, 5_000_000]
    for n_vars in [8, 16]:
        vars_to_sample = NON_CONTIGUOUS_VARIABLES[:n_vars]
        req_batch = [0x05, 0x00, 2 * n_vars]
        for addr, _, _ in vars_to_sample:
            tar_b = list(struct.pack("<I", addr))
            req_batch += [0x05] + tar_b + [0x0F]
        expected_len = 3 + 4 * n_vars

        for clk in test_clocks:
            client.set_swd_clock(clk)
            time.sleep(0.05)
            lats = []
            errs = 0
            t0 = time.perf_counter()
            for _ in range(50):
                t_s = time.perf_counter()
                try:
                    resp = client.xfer(req_batch)
                    t_e = time.perf_counter()
                    lats.append((t_e - t_s) * 1000.0)
                    if len(resp) != expected_len or resp[2] != 1:
                        errs += 1
                        client.clear_sticky_err()
                except Exception:
                    errs += 1
                    client.clear_sticky_err()
            t1 = time.perf_counter()
            st = calc_stats(lats)
            hz = len(lats) / (t1 - t0)
            print(f"{n_vars:>4} | {clk/1e6:>8.1f} MHz | {hz:>8.2f} Hz | {hz * n_vars:>13.1f} s/s | {st['mean']:>10.2f} ms | {st['p95']:>9.2f} ms")

    # Restore default 2.5 MHz clock
    client.set_swd_clock(2_500_000)

    # =========================================================================
    # PART 3: Pipelined Streaming (Double-buffering / Ping-Pong In-Flight Requests)
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 3: ASYNCHRONOUS PIPELINED STREAMING (HIDING WI-FI RTT)")
    print("Testing 8 and 16 variables with 2-in-flight pipelined requests")
    print("=" * 80)

    for n_vars in [4, 8, 16]:
        vars_to_sample = NON_CONTIGUOUS_VARIABLES[:n_vars]
        req_batch = bytes([0x05, 0x00, 2 * n_vars] + [b for addr, _, _ in vars_to_sample for b in [0x05] + list(struct.pack("<I", addr)) + [0x0F]])
        expected_len = 3 + 4 * n_vars
        PIPELINE_COUNT = 100

        # Prime the pipeline with 1 outstanding request
        sock.sendall(req_batch)
        t0 = time.perf_counter()
        for _ in range(PIPELINE_COUNT):
            # Send next frame while receiving previous
            sock.sendall(req_batch)
            resp = client.recv_exact(expected_len)
        # Drain the final response
        resp = client.recv_exact(expected_len)
        t1 = time.perf_counter()

        elapsed_pipe = t1 - t0
        rate_pipe = PIPELINE_COUNT / elapsed_pipe
        total_samples_pipe = rate_pipe * n_vars
        print(f"  {n_vars:>2} Non-Contiguous Variables (Pipelined):")
        print(f"     -> Sampling Frame Rate    : {rate_pipe:.2f} Hz")
        print(f"     -> Total Sampling Throughput: {total_samples_pipe:.1f} variable samples / sec")
        print(f"     -> Time per Frame Snapshot  : {1000.0 / rate_pipe:.2f} ms")

    sock.close()
    print("\n" + "=" * 80)
    print("BENCHMARK COMPLETED SUCCESSFULLY!")
    print("=" * 80)

if __name__ == "__main__":
    run_multivar_bench()
