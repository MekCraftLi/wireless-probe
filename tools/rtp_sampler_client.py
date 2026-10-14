#!/usr/bin/env python3
"""
RTSP / RTP Autonomous Telemetry Client for Wireless Probe (RP2350 + ESP32-C5)
=============================================================================
Architecture:
  - Control Plane (RTSP-like): TCP Port 3240 (CMSIS-DAP / UFMF_CH_CTRL)
      * RTSP_CMD_SETUP (0x80): Configure memory targets, mode, rate, burst size
      * RTSP_CMD_PLAY (0x81): Start autonomous high-speed SWD sampling
      * RTSP_CMD_TEARDOWN (0x82): Stop sampling
      * RTSP_CMD_GET_STATS (0x83): Retrieve probe hardware statistics
  - Data Plane (RTP-like): UDP Port 3242 (Zero-RTT, zero-ACK high-speed datagrams)
      * RTP Header (12 Bytes): Magic, Payload Type, Seq#, Hardware Timestamp (us), Sample Count
      * Payload: Continuous 32-bit sample values directly read from target via SWD

Usage:
  python tools/rtp_sampler_client.py --host 192.168.4.1 --mode block --addr 0x20000000 --burst 128 --duration 5
  python tools/rtp_sampler_client.py --host 192.168.4.1 --mode multivar --rate 1000 --duration 5
"""

import sys
import time
import socket
import struct
import argparse
from typing import List, Dict, Any

# Protocol Constants
RTP_MAGIC               = 0x80
RTP_PT_RAW_WORDS        = 0x60
RTP_PT_MULTIVAR         = 0x61

RTSP_CMD_SETUP          = 0x80
RTSP_CMD_PLAY           = 0x81
RTSP_CMD_TEARDOWN       = 0x82
RTSP_CMD_GET_STATS      = 0x83

SAMPLER_MODE_BLOCK      = 0x00
SAMPLER_MODE_MULTIVAR   = 0x01

# Default test addresses for STM32H7
DEFAULT_TEST_ADDRS = [
    0x20000000,  # DTCM base
    0x20000010,  # DTCM + 16B
    0x20000020,  # DTCM + 32B
    0x20000040,  # DTCM + 64B
    0x24000000,  # AXI SRAM base
    0x24000010,  # AXI SRAM + 16B
    0x38000000,  # SRAM4 base
    0x5C001000,  # DBGMCU_IDC (Device ID)
]


class RTPSamplerClient:
    def __init__(self, host: str = "192.168.4.1", tcp_port: int = 3240, udp_port: int = 3242):
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.tcp_sock = None
        self.udp_sock = None

    def connect(self, timeout: float = 3.0):
        """Connect to TCP control channel and bind UDP receiver."""
        print(f"[*] Connecting to TCP Control Plane at {self.host}:{self.tcp_port}...")
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.tcp_sock.settimeout(timeout)
        self.tcp_sock.connect((self.host, self.tcp_port))
        print(f"[+] TCP Control Plane connected.")

        print(f"[*] Binding UDP Data Plane on 0.0.0.0:{self.udp_port}...")
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Increase OS UDP receive buffer to 4MB to prevent drops
        try:
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except Exception:
            pass
        self.udp_sock.bind(("0.0.0.0", self.udp_port))
        self.udp_sock.settimeout(1.0)
        print(f"[+] UDP Data Plane ready.")

        # Send a registration UDP ping to probe so ESP32-C5 learns local UDP port immediately
        self.udp_sock.sendto(b"PING", (self.host, self.udp_port))

    def setup(self, mode: int, addrs: List[int], burst_words: int = 128, rate_hz: int = 0) -> Dict[str, Any]:
        """Send RTSP_CMD_SETUP over TCP."""
        var_count = len(addrs)
        payload = struct.pack("<BBBH I", RTSP_CMD_SETUP, mode, var_count, burst_words, rate_hz)
        for addr in addrs:
            payload += struct.pack("<I", addr)

        self.tcp_sock.sendall(payload)
        resp = self.tcp_sock.recv(64)
        if len(resp) < 8 or resp[0] != RTSP_CMD_SETUP:
            raise RuntimeError(f"SETUP failed, invalid response: {resp.hex()}")

        cmd_id, status, session_id, max_sps = struct.unpack("<BBHI", resp[:8])
        if status != 0:
            raise RuntimeError(f"SETUP rejected with status code: {status}")

        return {
            "session_id": session_id,
            "max_wire_sps": max_sps,
            "burst_words": burst_words,
            "mode": "BLOCK" if mode == SAMPLER_MODE_BLOCK else "MULTIVAR",
            "rate_hz": rate_hz,
        }

    def play(self):
        """Send RTSP_CMD_PLAY over TCP to start autonomous SWD sampling."""
        req = struct.pack("<B", RTSP_CMD_PLAY)
        self.tcp_sock.sendall(req)
        resp = self.tcp_sock.recv(16)
        if len(resp) < 2 or resp[0] != RTSP_CMD_PLAY or resp[1] != 0:
            raise RuntimeError(f"PLAY rejected: {resp.hex()}")

    def teardown(self):
        """Send RTSP_CMD_TEARDOWN over TCP to stop sampling."""
        try:
            req = struct.pack("<B", RTSP_CMD_TEARDOWN)
            self.tcp_sock.sendall(req)
            resp = self.tcp_sock.recv(16)
        except Exception:
            pass

    def get_stats(self) -> Dict[str, int]:
        """Query probe hardware stats via RTSP_CMD_GET_STATS."""
        req = struct.pack("<B", RTSP_CMD_GET_STATS)
        self.tcp_sock.sendall(req)
        resp = self.tcp_sock.recv(64)
        if len(resp) < 25 or resp[0] != RTSP_CMD_GET_STATS:
            return {}

        frames, samples, total_bytes, swd_err, buf_overrun, wire_sps = struct.unpack("<IIIIII", resp[1:25])
        return {
            "probe_total_frames": frames,
            "probe_total_samples": samples,
            "probe_total_bytes": total_bytes,
            "probe_swd_errors": swd_err,
            "probe_buffer_overruns": buf_overrun,
            "probe_wire_sps": wire_sps,
        }

    def close(self):
        if self.tcp_sock:
            self.tcp_sock.close()
        if self.udp_sock:
            self.udp_sock.close()


def run_benchmark(host: str, mode_str: str, addr_val: int, burst_words: int, rate_hz: int, duration_sec: float):
    mode = SAMPLER_MODE_BLOCK if mode_str == "block" else SAMPLER_MODE_MULTIVAR
    addrs = [addr_val] if mode == SAMPLER_MODE_BLOCK else DEFAULT_TEST_ADDRS[:8]

    client = RTPSamplerClient(host=host)
    try:
        client.connect()
        cfg = client.setup(mode=mode, addrs=addrs, burst_words=burst_words, rate_hz=rate_hz)
        print("\n" + "=" * 70)
        print("  RTSP / RTP AUTONOMOUS TELEMETRY BENCHMARK")
        print("=" * 70)
        print(f"  Target Mode      : {cfg['mode']}")
        print(f"  Addresses        : {[hex(a) for a in addrs]}")
        print(f"  Burst Words/Pkt  : {cfg['burst_words']} words ({cfg['burst_words'] * 4} bytes payload)")
        print(f"  Requested Rate   : {'Wire Saturation (MAX)' if rate_hz == 0 else f'{rate_hz} Hz'}")
        print(f"  Probe Est. Max   : {cfg['max_wire_sps']:,} SPS")
        print("=" * 70)

        input("\nPress ENTER to start autonomous streaming...")
        print("[*] Starting RTSP PLAY stream...")
        client.play()

        start_time = time.time()
        last_report = start_time
        total_packets = 0
        total_bytes = 0
        total_samples = 0
        lost_packets = 0
        last_seq = None

        timestamps = []

        print("\n[+] STREAM ACTIVE! Receiving high-speed RTP datagrams over UDP...")
        while time.time() - start_time < duration_sec:
            try:
                data, _ = client.udp_sock.recvfrom(2048)
            except socket.timeout:
                continue

            if len(data) < 12:
                continue

            magic, pt, seq, ts_us, session_id, sample_cnt = struct.unpack("<BBHIHH", data[:12])
            if magic != RTP_MAGIC:
                continue

            # Track sequence discontinuity
            if last_seq is not None:
                expected_seq = (last_seq + 1) & 0xFFFF
                if seq != expected_seq:
                    gap = (seq - expected_seq) & 0xFFFF
                    lost_packets += gap
            last_seq = seq

            total_packets += 1
            total_bytes += len(data)
            total_samples += sample_cnt
            timestamps.append(ts_us)

            # Periodic status line every 1 second
            now = time.time()
            if now - last_report >= 1.0:
                elapsed = now - start_time
                cur_rate_kbytes = (total_bytes / 1024.0) / elapsed
                cur_sps = total_samples / elapsed
                loss_pct = (lost_packets / (total_packets + lost_packets)) * 100.0 if (total_packets + lost_packets) > 0 else 0.0

                sys.stdout.write(
                    f"\r[{elapsed:4.1f}s] Pkts: {total_packets:6d} | Samples: {total_samples:8d} | "
                    f"Rate: {cur_rate_kbytes:7.2f} KB/s ({cur_sps:8.1f} SPS) | Loss: {lost_packets:2d} ({loss_pct:.2f}%)"
                )
                sys.stdout.flush()
                last_report = now

        client.teardown()
        elapsed_total = time.time() - start_time
        print("\n\n" + "-" * 70)
        print("  BENCHMARK COMPLETED")
        print("-" * 70)

        stats = client.get_stats()
        avg_throughput_kb = (total_bytes / 1024.0) / elapsed_total
        avg_sps = total_samples / elapsed_total

        print(f"  Duration           : {elapsed_total:.2f} s")
        print(f"  Total UDP Packets  : {total_packets:,}")
        print(f"  Total Data Bytes   : {total_bytes:,} bytes ({total_bytes / (1024*1024):.2f} MB)")
        print(f"  Total Samples Read : {total_samples:,} 32-bit words")
        print(f"  Effective Bandwidth: {avg_throughput_kb:.2f} KB/s ({avg_throughput_kb * 8 / 1024:.2f} Mbps)")
        print(f"  Sampling Frequency : {avg_sps:,.1f} Samples/sec")
        print(f"  Packet Loss        : {lost_packets} packets ({(lost_packets / (total_packets + lost_packets) * 100.0) if (total_packets + lost_packets) > 0 else 0.0:.3f}%)")

        if stats:
            print("\n  [Probe Hardware Counters]")
            print(f"  Probe Frames Sent  : {stats.get('probe_total_frames', 0):,}")
            print(f"  Probe Samples Read : {stats.get('probe_total_samples', 0):,}")
            print(f"  Probe SWD Errors   : {stats.get('probe_swd_errors', 0)}")
            print(f"  Probe Overruns     : {stats.get('probe_buffer_overruns', 0)}")
            print(f"  Probe Wire SPS     : {stats.get('probe_wire_sps', 0):,} SPS")

        # SWD wire saturation evaluation
        swd_max_kb = 869.5  # 10 MHz theoretical limit
        wire_saturation = (avg_throughput_kb / swd_max_kb) * 100.0
        print("\n" + "=" * 70)
        print(f"  SWD BUS SATURATION EFFICIENCY: {min(wire_saturation, 100.0):.1f}% of physical wire limit")
        print("=" * 70)

    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="RTSP/RTP Autonomous High-Speed SWD Telemetry Client")
    parser.add_argument("--host", default="192.168.4.1", help="Target probe IP address")
    parser.add_argument("--mode", choices=["block", "multivar"], default="block", help="Sampling mode")
    parser.add_argument("--addr", default="0x20000000", help="Target memory address for block mode (hex)")
    parser.add_argument("--burst", type=int, default=128, help="Words per UDP packet (default: 128 words = 512B)")
    parser.add_argument("--rate", type=int, default=0, help="Target rate in Hz (0 = unconstrained wire saturation)")
    parser.add_argument("--duration", type=float, default=5.0, help="Benchmark duration in seconds")

    args = parser.parse_args()
    addr_val = int(args.addr, 16) if args.addr.startswith("0x") else int(args.addr)

    run_benchmark(
        host=args.host,
        mode_str=args.mode,
        addr_val=addr_val,
        burst_words=args.burst,
        rate_hz=args.rate,
        duration_sec=args.duration
    )


if __name__ == "__main__":
    main()
