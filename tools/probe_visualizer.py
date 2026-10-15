#!/usr/bin/env python3
"""
MekProbe Real-time Visualizer & Performance Telemetry Bridge
- Hardware Autonomous Streaming: Uses RTSP control (TCP 3240) + RTP/UDP data push (UDP 3242)
- Zero Ping-Pong Round-Trip: Pico 2 Core 0 and DMA autonomously read STM32H7 AXI SRAM (0x24000000)
- True SWD Wire-Speed Saturation: Up to 10 MHz SWCLK -> 217.4 kSa/s (13,586 frames/s)
- Comprehensive SWD Bus Bandwidth & Clock Metrics Dashboard
"""

import argparse
import http.server
import json
import math
import os
import queue
import socket
import struct
import sys
import threading
import time
import urllib.parse

# Enable Windows 1ms high-precision multimedia timer
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

PROBE_IP = "192.168.4.1"
PROBE_TCP_PORT = 3240
PROBE_UDP_PORT = 3242
WEB_PORT = 8080
TARGET_ADDR = 0x24000000
TELEMETRY_MAGIC = 0x54454C4D
TEST_PATTERN = 0xA5A55A5A

RTSP_CMD_SETUP = 0x80
RTSP_CMD_PLAY = 0x81
RTSP_CMD_TEARDOWN = 0x82
RTSP_CMD_GET_STATS = 0x83
RTP_MAGIC = 0x80

WORDS_PER_FRAME = 16  # 16 x 32-bit words = 64 bytes TelemetryData_t
BYTES_PER_FRAME = 64
SWD_CYCLES_PER_WORD = 46 # ARM CoreSight SWD block autoincrement: 8 req + 3 ack + 32 data + 1 par + 2 trn

# Global State
g_clients = []
g_clients_lock = threading.Lock()
g_swd_clock_hz = 2000000  # Default 2.0 MHz (43.5 kSa/s)
g_sampling_mode = "custom"  # "custom" (Mode 1: configurable rate) or "max" (Mode 2: wire speed)
g_custom_rate_hz = 2000     # Default 2,000 Hz frame rate in custom mode (matching STM32 2kHz)
g_collector = None

g_target_status = {
    "mode": "simulation",
    "dpidr": "None",
    "target": "Awaiting STM32H7 Connection",
    "swd_clock_hz": g_swd_clock_hz,
    "swd_clock_str": "2.0 MHz",
    "sampling_mode": g_sampling_mode,
    "custom_rate_hz": g_custom_rate_hz,
    "probe_rate_param": g_custom_rate_hz,
    "active_fps": g_custom_rate_hz,
    "active_sps": g_custom_rate_hz * WORDS_PER_FRAME,
    "theoretical_max_sps": g_swd_clock_hz // SWD_CYCLES_PER_WORD,
    "theoretical_max_fps": (g_swd_clock_hz // SWD_CYCLES_PER_WORD) // WORDS_PER_FRAME,
    "theoretical_payload_kbps": round((g_swd_clock_hz * 4 / SWD_CYCLES_PER_WORD) / 1024, 1),
    "active_payload_kbps": round((g_custom_rate_hz * BYTES_PER_FRAME) / 1024, 1),
    "theoretical_wire_mbps": round(g_swd_clock_hz / 1000000.0, 2),
    "bus_utilization_pct": 73.6
}
g_force_reconnect = threading.Event()
g_running = True

def update_swd_specs(clock_hz=None, mode=None, custom_rate_hz=None):
    global g_swd_clock_hz, g_sampling_mode, g_custom_rate_hz
    if clock_hz is not None:
        g_swd_clock_hz = clock_hz
    if mode is not None and mode in ["custom", "max"]:
        g_sampling_mode = mode
    if custom_rate_hz is not None and custom_rate_hz > 0:
        g_custom_rate_hz = int(custom_rate_hz)

    clock = g_swd_clock_hz
    if clock >= 1000000:
        str_val = f"{clock/1000000:.1f} MHz" if clock % 1000000 == 0 else f"{clock/1000000:.2f} MHz"
    else:
        str_val = f"{clock//1000} kHz"

    max_sps = clock // SWD_CYCLES_PER_WORD
    max_fps = max_sps // WORDS_PER_FRAME
    max_payload_kbps = (clock * 4 / SWD_CYCLES_PER_WORD) / 1024.0
    wire_mbps = clock / 1000000.0

    if g_sampling_mode == "max":
        active_fps = max_fps
        active_sps = max_sps
        active_payload_kbps = round(max_payload_kbps, 1)
        bus_util_pct = 98.4
        probe_rate_param = 0  # 0 indicates wire speed saturation to probe firmware
    else:
        active_fps = min(g_custom_rate_hz, max_fps)
        active_sps = active_fps * WORDS_PER_FRAME
        active_payload_kbps = round((active_fps * BYTES_PER_FRAME) / 1024.0, 1)
        needed_cycles = active_fps * WORDS_PER_FRAME * SWD_CYCLES_PER_WORD
        bus_util_pct = min(100.0, round((needed_cycles / clock) * 100.0, 1))
        probe_rate_param = g_custom_rate_hz

    g_target_status["swd_clock_hz"] = clock
    g_target_status["swd_clock_str"] = str_val
    g_target_status["sampling_mode"] = g_sampling_mode
    g_target_status["custom_rate_hz"] = g_custom_rate_hz
    g_target_status["probe_rate_param"] = probe_rate_param
    g_target_status["active_fps"] = active_fps
    g_target_status["active_sps"] = active_sps
    g_target_status["theoretical_max_sps"] = max_sps
    g_target_status["theoretical_max_fps"] = max_fps
    g_target_status["theoretical_payload_kbps"] = round(max_payload_kbps, 1)
    g_target_status["active_payload_kbps"] = active_payload_kbps
    g_target_status["theoretical_wire_mbps"] = round(wire_mbps, 2)
    g_target_status["bus_utilization_pct"] = bus_util_pct

def parse_telemetry_packet(raw_bytes):
    """Parse 64-byte TelemetryData_t structure."""
    if len(raw_bytes) < 64:
        return None
    try:
        fields = struct.unpack("<IIiffffffIIIIII", raw_bytes[:64])
        return {
            "magic": f"0x{fields[0]:08X}",
            "seq": fields[1],
            "timestamp_us": fields[2],
            "sine": round(fields[3], 2),
            "tri": round(fields[4], 2),
            "sq": round(fields[5], 2),
            "pid_sp": round(fields[6], 2),
            "pid_pv": round(fields[7], 2),
            "pid_out": round(fields[8], 2),
            "noise": round(fields[9], 2),
            "cpu_load": fields[10],
            "counter": fields[11],
            "pattern": f"0x{fields[12]:08X}"
        }
    except Exception:
        return None

def broadcast_sse(data_dict):
    """Broadcast JSON payload to connected SSE clients without dropping connections."""
    msg = f"data: {json.dumps(data_dict)}\n\n".encode("utf-8")
    with g_clients_lock:
        for q in g_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

class HardwareSamplerBridge:
    """
    Manages TCP 3240 RTSP Control Channel and UDP 3242 Autonomous Data Stream.
    Full hardware wire-speed saturation with zero polling.
    """
    def __init__(self, probe_ip=PROBE_IP):
        self.probe_ip = probe_ip
        self.tcp_sock = None
        self.udp_sock = None
        self.is_active = False
        self.lock = threading.Lock()

    def xfer(self, req):
        self.tcp_sock.sendall(bytes(req))
        return self.tcp_sock.recv(1024)

    def attach_and_start(self, clock_hz, rate_param=0):
        """Perform SWD connect, set clock, CoreSight debug power-up, and start autonomous streaming."""
        with self.lock:
            try:
                # 1. Open TCP Control Plane
                self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_sock.settimeout(1.5)
                self.tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.tcp_sock.connect((self.probe_ip, PROBE_TCP_PORT))

                # 2. Open UDP Data Plane
                self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
                except Exception:
                    pass
                self.udp_sock.bind(("0.0.0.0", PROBE_UDP_PORT))
                self.udp_sock.settimeout(0.5)

                # Send registration datagram so ESP32-C5 learns local port immediately
                self.udp_sock.sendto(b"PING", (self.probe_ip, PROBE_UDP_PORT))

                # 3. Standard SWD Reset & Synchronization
                self.xfer([0x03]) # DAP_Disconnect
                r = self.xfer([0x02, 0x01]) # DAP_Connect SWD
                if len(r) < 2 or r[1] != 1:
                    self.teardown()
                    return False

                # Configure SWD Clock dynamically
                clk_bytes = list(struct.pack("<I", clock_hz))
                self.xfer([0x11] + clk_bytes)
                self.xfer([0x04, 0x00, 0x40, 0x00, 0x00, 0x00])

                # SWD Line Reset
                self.xfer([0x12, 56] + [0xFF]*7)
                self.xfer([0x12, 16, 0x9E, 0xE7])
                self.xfer([0x12, 56] + [0xFF]*7)
                self.xfer([0x12, 16, 0x00, 0x00])

                # Read DPIDR
                r = self.xfer([0x05, 0x00, 0x01, 0x02])
                if len(r) < 7 or r[1] != 1 or (r[2] & 0x07) != 1:
                    self.teardown()
                    return False

                dpidr = struct.unpack("<I", r[3:7])[0]
                print(f"[Bridge] >>> TARGET DETECTED! DPIDR = 0x{dpidr:08X} (SWD Clock: {g_target_status['swd_clock_str']}) <<<")

                # Power up CoreSight debug subsystem (DP_CTRL_STAT)
                self.xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])

                # 4. Configure RTSP Autonomous Sampler
                # mode=0 (BLOCK), var_count=1, burst_words=16 (64 bytes), rate_hz=rate_param (0=Wire Speed)
                setup_pkt = struct.pack("<BBBHII", RTSP_CMD_SETUP, 0, 1, 16, rate_param, TARGET_ADDR)
                r = self.xfer(setup_pkt)
                if len(r) < 2 or r[0] != RTSP_CMD_SETUP or r[1] != 0:
                    print(f"[Bridge] SETUP rejected: {r.hex()}")
                    self.teardown()
                    return False

                # 5. Start Autonomous Stream via PLAY
                r = self.xfer([RTSP_CMD_PLAY])
                if len(r) < 2 or r[0] != RTSP_CMD_PLAY or r[1] != 0:
                    print(f"[Bridge] PLAY rejected: {r.hex()}")
                    self.teardown()
                    return False

                print(f"[Bridge] [+] RTSP/RTP Autonomous Hardware Stream ACTIVE! (UDP -> 0.0.0.0:{PROBE_UDP_PORT}, Rate={rate_param}Hz)")
                self.is_active = True
                g_target_status["mode"] = "hardware_swd"
                g_target_status["dpidr"] = f"0x{dpidr:08X}"
                g_target_status["target"] = "ARM Cortex-M7 (STM32H723ZGTx Autonomous UDP Stream)"
                broadcast_sse({"type": "status", **g_target_status})
                return True

            except Exception:
                self.teardown()
                return False

    def reconfigure_sampler(self, rate_param):
        """Dynamically update autonomous sampler rate without dropping SWD connection."""
        with self.lock:
            if not self.is_active or not self.tcp_sock:
                return False
            try:
                # 1. Stop current stream
                self.tcp_sock.sendall(bytes([RTSP_CMD_TEARDOWN]))
                time.sleep(0.005)
                try:
                    self.tcp_sock.recv(16)
                except Exception:
                    pass
                # 2. Re-setup with new rate parameter
                setup_pkt = struct.pack("<BBBHII", RTSP_CMD_SETUP, 0, 1, 16, rate_param, TARGET_ADDR)
                self.tcp_sock.sendall(setup_pkt)
                r = self.tcp_sock.recv(64)
                if len(r) < 2 or r[0] != RTSP_CMD_SETUP or r[1] != 0:
                    print(f"[Bridge] Reconfigure SETUP rejected: {r.hex()}")
                    return False
                # 3. Resume streaming
                self.tcp_sock.sendall(bytes([RTSP_CMD_PLAY]))
                r = self.tcp_sock.recv(16)
                if len(r) < 2 or r[0] != RTSP_CMD_PLAY or r[1] != 0:
                    print(f"[Bridge] Reconfigure PLAY rejected: {r.hex()}")
                    return False
                print(f"[Bridge] Autonomous sampler reconfigured: rate_param={rate_param} Hz")
                return True
            except Exception as e:
                print(f"[Bridge] Reconfigure error: {e}")
                return False

    def teardown(self):
        self.is_active = False
        if self.tcp_sock:
            try:
                self.tcp_sock.sendall(bytes([RTSP_CMD_TEARDOWN]))
            except Exception:
                pass
            try:
                self.tcp_sock.close()
            except Exception:
                pass
            self.tcp_sock = None
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None

    def recv_autonomous_packet(self):
        """Receive an autonomous RTP packet over UDP."""
        if not self.is_active or not self.udp_sock:
            return None
        try:
            data, _ = self.udp_sock.recvfrom(2048)
            if len(data) >= 76 and data[0] == RTP_MAGIC:
                # data[0:12] RTP header, data[12:76] 64-byte TelemetryData_t
                return parse_telemetry_packet(data[12:76])
        except socket.timeout:
            return None
        except Exception:
            return None
        return None

class TelemetryCollector(threading.Thread):
    def __init__(self, probe_ip=PROBE_IP, force_sim=False):
        super().__init__(daemon=True)
        self.bridge = HardwareSamplerBridge(probe_ip=probe_ip)
        self.force_sim = force_sim

    def run(self):
        print(f"[Collector] Telemetry Engine initialized. Current Clock: {g_target_status['swd_clock_str']}")

        sim_seq = 0
        sim_pos = 0.0
        sim_vel = 0.0
        sim_integral = 0.0
        sim_last_err = 0.0
        lfsr = 0x12345678

        FRAME_PERIOD = 0.01667  # 60 FPS frame time (16.67 ms)
        next_frame_time = time.perf_counter()
        last_detect_time = 0

        while g_running:
            # 1. Background Hardware Target Attachment or Clock Switch
            now = time.perf_counter()
            if g_force_reconnect.is_set():
                g_force_reconnect.clear()
                self.bridge.teardown()
                last_detect_time = 0

            if not self.bridge.is_active and not self.force_sim:
                if now - last_detect_time >= 2.0:
                    last_detect_time = now
                    self.bridge.attach_and_start(g_swd_clock_hz, g_target_status["probe_rate_param"])

            batch_samples = []

            # 2. Hardware Autonomous Stream Mode (Zero Ping-Pong!)
            if self.bridge.is_active:
                drain_start = time.perf_counter()
                while time.perf_counter() - drain_start < 0.014:
                    pkt = self.bridge.recv_autonomous_packet()
                    if pkt:
                        batch_samples.append(pkt)
                    else:
                        break

                if not batch_samples and (now - last_detect_time > 5.0):
                    print("[Collector] No UDP datagrams received. Reconnecting stream...")
                    self.bridge.teardown()
                    g_target_status["mode"] = "simulation"
                    broadcast_sse({"type": "status", **g_target_status})

            # 3. Fallback / Demonstration Mode (Simulating SWD clock speed and active mode)
            if not self.bridge.is_active:
                target_fps = g_target_status["active_fps"]
                samples_in_frame = max(1, int(target_fps * FRAME_PERIOD))
                dt = 1.0 / max(1, target_fps)

                for _ in range(samples_in_frame):
                    sim_seq += 1
                    t = sim_seq * dt

                    sine = 100.0 * math.sin(2.0 * math.pi * 5.0 * t)
                    tri_phase = (t * 2.0) % 1.0
                    tri = (tri_phase * 200.0) if tri_phase < 0.5 else ((1.0 - tri_phase) * 200.0)
                    sq = 60.0 if (t % 1.0) < 0.5 else 0.0

                    pid_sp = 85.0 if (t % 4.0) < 2.0 else 25.0
                    err = pid_sp - sim_pos
                    sim_integral += err * dt
                    sim_integral = max(-50.0, min(50.0, sim_integral))
                    deriv = (err - sim_last_err) / dt
                    sim_last_err = err
                    pid_out = 3.2 * err + 1.5 * sim_integral + 0.08 * deriv
                    pid_out = max(-100.0, min(100.0, pid_out))

                    accel = (pid_out * 8.0 - 6.0 * sim_vel - 10.0 * sim_pos) / 1.5
                    sim_vel += accel * dt
                    sim_pos += sim_vel * dt

                    lfsr ^= (lfsr << 13) & 0xFFFFFFFF
                    lfsr ^= (lfsr >> 17) & 0xFFFFFFFF
                    lfsr ^= (lfsr << 5) & 0xFFFFFFFF
                    noise = ((lfsr & 0xFFFF) / 32768.0 - 1.0) * 6.0

                    batch_samples.append({
                        "magic": f"0x{TELEMETRY_MAGIC:08X}",
                        "seq": sim_seq,
                        "timestamp_us": int(t * 1000000),
                        "sine": round(sine, 2),
                        "tri": round(tri, 2),
                        "sq": round(sq, 2),
                        "pid_sp": round(pid_sp, 2),
                        "pid_pv": round(sim_pos, 2),
                        "pid_out": round(pid_out, 2),
                        "noise": round(noise, 2),
                        "cpu_load": int(20 + 8 * (1.0 + math.sin(t * 0.8))),
                        "counter": sim_seq * 13,
                        "pattern": f"0x{TEST_PATTERN:08X}"
                    })

            # 4. Broadcast Batch at 60 FPS
            if batch_samples:
                broadcast_sse({
                    "type": "batch",
                    "samples": batch_samples,
                    "swd": {
                        "clock_str": g_target_status["swd_clock_str"],
                        "clock_hz": g_swd_clock_hz,
                        "sampling_mode": g_target_status["sampling_mode"],
                        "custom_rate_hz": g_target_status["custom_rate_hz"],
                        "active_fps": g_target_status["active_fps"],
                        "active_sps": g_target_status["active_sps"],
                        "bus_utilization_pct": g_target_status["bus_utilization_pct"],
                        "wire_mbps": g_target_status["theoretical_wire_mbps"],
                        "payload_kbps": g_target_status["active_payload_kbps"],
                        "words_per_frame": WORDS_PER_FRAME
                    }
                })

            # 5. Precise 60 FPS Frame Pacing
            next_frame_time += FRAME_PERIOD
            now = time.perf_counter()
            sleep_time = next_frame_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_frame_time = now

class WebVisualizerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            html_path = os.path.join(os.path.dirname(__file__), "visualizer", "index.html")
            try:
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_error(500, f"Error reading index.html: {e}")
            return

        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_queue = queue.Queue(maxsize=120)
            with g_clients_lock:
                g_clients.append(client_queue)

            init_status = f"data: {json.dumps({'type': 'status', **g_target_status})}\n\n".encode("utf-8")
            try:
                self.wfile.write(init_status)
                self.wfile.flush()
            except Exception:
                with g_clients_lock:
                    if client_queue in g_clients:
                        g_clients.remove(client_queue)
                return

            try:
                while g_running:
                    try:
                        msg = client_queue.get(timeout=1.0)
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                with g_clients_lock:
                    if client_queue in g_clients:
                        g_clients.remove(client_queue)
            return

        elif self.path == "/api/stats":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(g_target_status).encode("utf-8"))
            return

        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == "/api/reconnect":
            g_force_reconnect.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "reconnect_triggered"}).encode("utf-8"))
            return

        elif self.path.startswith("/api/set_mode"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            mode = params.get("mode", [None])[0]
            rate = params.get("rate", [None])[0]
            rate_int = int(rate) if (rate and rate.isdigit()) else None

            if mode in ["custom", "max"]:
                update_swd_specs(mode=mode, custom_rate_hz=rate_int)
                rate_param = g_target_status["probe_rate_param"]

                global g_collector
                if g_collector and g_collector.bridge.is_active:
                    ok = g_collector.bridge.reconfigure_sampler(rate_param)
                    if not ok:
                        g_force_reconnect.set()

                print(f"[API] Sampler mode set to '{mode}' (Custom Rate: {g_target_status['custom_rate_hz']} Hz, rate_param={rate_param})")
                broadcast_sse({"type": "status", **g_target_status})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "mode": g_target_status["sampling_mode"],
                    "custom_rate_hz": g_target_status["custom_rate_hz"],
                    "active_sps": g_target_status["active_sps"],
                    "bus_utilization_pct": g_target_status["bus_utilization_pct"]
                }).encode("utf-8"))
                return
            self.send_error(400, "Invalid mode parameter")
            return

        elif self.path.startswith("/api/set_clock"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "hz" in params:
                try:
                    hz = int(params["hz"][0])
                    if hz in [500000, 1000000, 2000000, 5000000, 10000000]:
                        update_swd_specs(clock_hz=hz)
                        g_force_reconnect.set()
                        print(f"[API] SWD Clock switched to {g_target_status['swd_clock_str']} ({hz} Hz)")
                        broadcast_sse({"type": "status", **g_target_status})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "ok", "clock": g_target_status["swd_clock_str"]}).encode("utf-8"))
                        return
                except ValueError:
                    pass
            self.send_error(400, "Invalid clock parameter")
            return

        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        pass

def main():
    parser = argparse.ArgumentParser(description="MekProbe Real-time Visualizer & Performance Telemetry Bridge")
    parser.add_argument("--probe-ip", default=PROBE_IP, help="Wireless probe IP address (default: 192.168.4.1)")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Web dashboard port (default: 8080)")
    parser.add_argument("--clock", type=int, default=2000000, choices=[500000, 1000000, 2000000, 5000000, 10000000],
                        help="Initial SWD Clock frequency in Hz (default: 2000000 = 2.0 MHz)")
    parser.add_argument("--mode", default="custom", choices=["custom", "max"],
                        help="Sampling mode: 'custom' (configurable rate) or 'max' (wire speed saturation)")
    parser.add_argument("--rate", type=int, default=2000,
                        help="Sampling rate in Hz for custom mode (default: 2000)")
    parser.add_argument("--sim", action="store_true", help="Force local simulation mode without connecting to probe")
    args = parser.parse_args()

    update_swd_specs(clock_hz=args.clock, mode=args.mode, custom_rate_hz=args.rate)

    global g_collector
    collector = TelemetryCollector(probe_ip=args.probe_ip, force_sim=args.sim)
    g_collector = collector
    collector.start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), WebVisualizerHandler)
    print("\n" + "=" * 65)
    print(f"  MekProbe Wire-Speed Autonomous Streaming (Zero Ping-Pong)")
    print("=" * 65)
    print(f"  [+] Web Dashboard UI:     http://localhost:{args.port}")
    print(f"  [+] Active Mode:          {'模式一: 自主配置采样率' if args.mode == 'custom' else '模式二: 最大速率采样 (Wire-Speed)'}")
    print(f"  [+] Target Sample Rate:   {g_target_status['custom_rate_hz']} Hz (Active SPS: {g_target_status['active_sps']:,} Sa/s)")
    print(f"  [+] SWD Physical Clock:   {g_target_status['swd_clock_str']} ({args.clock} Hz)")
    print(f"  [+] SWD Wire Bitrate:     {g_target_status['theoretical_wire_mbps']} Mbps")
    print(f"  [+] Net Payload Capacity: {g_target_status['theoretical_payload_kbps']} KB/s (Max: {g_target_status['theoretical_max_sps']:,} Sa/s)")
    print(f"  [+] Control Channel:      TCP {args.probe_ip}:{PROBE_TCP_PORT} (RTSP)")
    print(f"  [+] Telemetry Channel:    UDP 0.0.0.0:{PROBE_UDP_PORT} (RTP Push)")
    print(f"  [+] Target Memory Addr:   0x{TARGET_ADDR:08X} (AXI SRAM RAM_D1)")
    print("=" * 65 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        global g_running
        g_running = False
        collector.bridge.teardown()
        server.server_close()

if __name__ == "__main__":
    main()
