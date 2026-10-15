#!/usr/bin/env python3
"""
MekProbe Real-time Visualizer & Performance Telemetry Bridge
- Hardware Autonomous Streaming: Uses RTSP control (TCP 3240) + RTP/UDP data push (UDP 3242)
- Zero Ping-Pong Round-Trip: Pico 2 Core 0 and DMA autonomously read STM32H7 AXI SRAM (0x24000000)
- 60 FPS Smooth Web Dashboard with HTML5 Canvas Oscilloscope & Real-time Metrics
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

# Global State
g_clients = []
g_clients_lock = threading.Lock()
g_target_status = {
    "mode": "simulation",
    "dpidr": "None",
    "target": "Awaiting STM32H7 Connection",
    "sps": 0,
    "total_samples": 0
}
g_force_reconnect = threading.Event()
g_running = True

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
    No request-response round-trips during sampling!
    """
    def __init__(self, probe_ip=PROBE_IP, target_sps=2000):
        self.probe_ip = probe_ip
        self.target_sps = target_sps
        self.tcp_sock = None
        self.udp_sock = None
        self.is_active = False
        self.lock = threading.Lock()

    def xfer(self, req):
        self.tcp_sock.sendall(bytes(req))
        return self.tcp_sock.recv(1024)

    def attach_and_start(self):
        """Perform SWD connect, CoreSight debug power-up, and start autonomous streaming."""
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
                    self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
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

                # Set Clock 500 kHz
                self.xfer([0x11, 0x20, 0xA1, 0x07, 0x00])
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
                print(f"[Bridge] >>> TARGET DETECTED! DPIDR = 0x{dpidr:08X} <<<")

                # Power up CoreSight debug subsystem (DP_CTRL_STAT)
                self.xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])

                # 4. Configure RTSP Autonomous Sampler
                # Format: [0x80] [mode:1B] [var_count:1B] [burst_words:2B] [rate_hz:4B] [addr:4B]
                # mode=0 (BLOCK), var_count=1, burst_words=16 (64 bytes), rate_hz=0 (wire speed saturation)
                setup_pkt = struct.pack("<BBBHII", RTSP_CMD_SETUP, 0, 1, 16, 0, TARGET_ADDR)
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

                print(f"[Bridge] [+] RTSP/RTP Autonomous Hardware Stream ACTIVE! (UDP -> 0.0.0.0:{PROBE_UDP_PORT})")
                self.is_active = True
                g_target_status["mode"] = "hardware_swd"
                g_target_status["dpidr"] = f"0x{dpidr:08X}"
                g_target_status["target"] = "ARM Cortex-M7 (STM32H723ZGTx Autonomous UDP Stream)"
                broadcast_sse({"type": "status", **g_target_status})
                return True

            except Exception as e:
                self.teardown()
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
        """Receive an autonomous RTP packet over UDP. Non-blocking/timeout."""
        if not self.is_active or not self.udp_sock:
            return None
        try:
            data, _ = self.udp_sock.recvfrom(2048)
            if len(data) >= 76 and data[0] == RTP_MAGIC:
                # data[0:12] is RTP Header: magic, pt, seq, ts_us, session_id, sample_count
                # data[12:76] is the 64-byte TelemetryData_t payload read autonomously by RP2350
                return parse_telemetry_packet(data[12:76])
        except socket.timeout:
            return None
        except Exception:
            return None
        return None

class TelemetryCollector(threading.Thread):
    def __init__(self, probe_ip=PROBE_IP, force_sim=False, target_sps=2000):
        super().__init__(daemon=True)
        self.bridge = HardwareSamplerBridge(probe_ip=probe_ip, target_sps=target_sps)
        self.force_sim = force_sim
        self.target_sps = target_sps

    def run(self):
        print(f"[Collector] Telemetry Engine initialized. Target rate: {self.target_sps} Sa/s")

        sim_seq = 0
        sim_pos = 0.0
        sim_vel = 0.0
        sim_integral = 0.0
        sim_last_err = 0.0
        lfsr = 0x12345678

        dt = 1.0 / self.target_sps
        FRAME_PERIOD = 0.01667  # 60 FPS frame time (16.67 ms)
        next_frame_time = time.perf_counter()
        last_detect_time = 0

        while g_running:
            # 1. Background Hardware Target Attachment
            now = time.perf_counter()
            if not self.bridge.is_active and not self.force_sim:
                if now - last_detect_time >= 2.5 or g_force_reconnect.is_set():
                    last_detect_time = now
                    g_force_reconnect.clear()
                    self.bridge.attach_and_start()

            batch_samples = []

            # 2. Hardware Autonomous Stream Mode (Zero Ping-Pong!)
            if self.bridge.is_active:
                # Drain available UDP datagrams pushed by probe
                drain_start = time.perf_counter()
                while time.perf_counter() - drain_start < 0.014: # Collect up to frame deadline
                    pkt = self.bridge.recv_autonomous_packet()
                    if pkt:
                        batch_samples.append(pkt)
                    else:
                        break

                if not batch_samples and (now - last_detect_time > 5.0):
                    # Check if stream died
                    print("[Collector] No UDP datagrams received. Reconnecting stream...")
                    self.bridge.teardown()
                    g_target_status["mode"] = "simulation"
                    broadcast_sse({"type": "status", **g_target_status})

            # 3. Fallback Simulation Mode
            if not self.bridge.is_active:
                samples_in_frame = max(1, int(self.target_sps * FRAME_PERIOD))
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
                    "samples": batch_samples
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
            except (BrokenPipeError, ConnectionResetError):
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
        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        pass

def main():
    parser = argparse.ArgumentParser(description="MekProbe Real-time Visualizer & Performance Telemetry Bridge")
    parser.add_argument("--probe-ip", default=PROBE_IP, help="Wireless probe IP address (default: 192.168.4.1)")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Web dashboard port (default: 8080)")
    parser.add_argument("--sps", type=int, default=2000, help="Target sampling rate (default: 2000 Sa/s)")
    parser.add_argument("--sim", action="store_true", help="Force local simulation mode without connecting to probe")
    args = parser.parse_args()

    collector = TelemetryCollector(probe_ip=args.probe_ip, force_sim=args.sim, target_sps=args.sps)
    collector.start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), WebVisualizerHandler)
    print("\n" + "=" * 65)
    print(f"  MekProbe Autonomous Hardware Streaming (Zero Ping-Pong)")
    print("=" * 65)
    print(f"  [+] Web Dashboard UI:     http://localhost:{args.port}")
    print(f"  [+] Control Channel (TCP): {args.probe_ip}:{PROBE_TCP_PORT} (RTSP)")
    print(f"  [+] Data Channel (UDP):    0.0.0.0:{PROBE_UDP_PORT} (RTP Push)")
    print(f"  [+] Target Memory Addr:   0x{TARGET_ADDR:08X} (AXI SRAM RAM_D1)")
    print(f"  [+] Autonomous Rate:      {args.sps} Sa/s (Probe-Driven Wire Speed)")
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
