#!/usr/bin/env python3
"""
MekProbe Real-time Visualizer & Performance Telemetry Bridge
- Collects live multi-channel waveforms from STM32H7 AXI SRAM (0x24000000) over Wi-Fi 6
- Serves an ultra-fast 60 FPS HTML5 Canvas Oscilloscope & Real-time Metrics Dashboard
- Provides seamless zero-dependency HTTP + Server-Sent Events (SSE) server
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

PROBE_IP = "192.168.4.1"
PROBE_TCP_PORT = 3240
WEB_PORT = 8080
TARGET_ADDR = 0x24000000
TELEMETRY_MAGIC = 0x54454C4D
TEST_PATTERN = 0xA5A55A5A

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
    """
    Parse 64-byte TelemetryData_t:
    uint32 magic;
    uint32 seq;
    uint32 timestamp_us;
    float  sine;
    float  triangle;
    float  square;
    float  pid_sp;
    float  pid_pv;
    float  pid_out;
    float  noise;
    uint32 cpu_load;
    uint32 counter;
    uint32 pattern;
    uint32 reserved[3];
    """
    if len(raw_bytes) < 64:
        return None
    try:
        fields = struct.unpack("<IIiffffffIIIIII", raw_bytes[:64])
        return {
            "type": "telemetry",
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
    """Broadcast JSON payload to all connected SSE browser clients."""
    msg = f"data: {json.dumps(data_dict)}\n\n".encode("utf-8")
    with g_clients_lock:
        dead_clients = []
        for q in g_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead_clients.append(q)
        for d in dead_clients:
            if d in g_clients:
                g_clients.remove(d)

class TelemetryCollector(threading.Thread):
    def __init__(self, probe_ip=PROBE_IP, force_sim=False):
        super().__init__(daemon=True)
        self.probe_ip = probe_ip
        self.force_sim = force_sim
        self.sock = None
        self.is_hw_connected = False

    def xfer(self, req):
        self.sock.sendall(bytes(req))
        return self.sock.recv(1024)

    def init_swd(self):
        """Try initializing SWD and reading DPIDR."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(2.0)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.connect((self.probe_ip, PROBE_TCP_PORT))

            # 1. DAP_Disconnect then Connect
            self.xfer([0x03])
            r = self.xfer([0x02, 0x01]) # Port SWD = 1
            if len(r) < 2 or r[1] != 1:
                self.sock.close()
                return False

            # 2. Clock 500 kHz
            self.xfer([0x11, 0x20, 0xA1, 0x07, 0x00])

            # 3. DAP_TransferConfigure (idle=0, retry=64)
            self.xfer([0x04, 0x00, 0x40, 0x00, 0x00, 0x00])

            # 4. Standard SWD Reset sequence
            self.xfer([0x12, 56] + [0xFF]*7)
            self.xfer([0x12, 16, 0x9E, 0xE7])
            self.xfer([0x12, 56] + [0xFF]*7)
            self.xfer([0x12, 16, 0x00, 0x00])

            # 5. Read DPIDR
            r = self.xfer([0x05, 0x00, 0x01, 0x02])
            if len(r) >= 7 and r[1] == 1 and (r[2] & 0x07) == 1:
                dpidr = struct.unpack("<I", r[3:7])[0]
                g_target_status["dpidr"] = f"0x{dpidr:08X}"
                g_target_status["target"] = "ARM Cortex-M7 (STM32H7)"
                print(f"[Collector] SWD Target Detected! DPIDR = 0x{dpidr:08X}")

                # Power Up CoreSight Debug
                self.xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
                # Select AP0
                self.xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
                # AP0 CSW = 0x23000052 (32-bit word, auto-increment single)
                self.xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])
                return True
            else:
                self.sock.close()
                return False
        except Exception as e:
            if self.sock:
                try: self.sock.close()
                except: pass
            return False

    def read_telemetry_block(self):
        """Read 16 words (64 bytes) starting at TARGET_ADDR via DAP_TransferBlock."""
        try:
            # Set TAR = TARGET_ADDR
            addr_bytes = list(struct.pack("<I", TARGET_ADDR))
            self.xfer([0x05, 0x00, 0x01, 0x05] + addr_bytes)

            # DAP_TransferBlock: ap=0, count=16 words, RnW
            # [0x06, dap_index=0, count_lsb=16, count_msb=0, request=0x07 (AP, RnW)]
            self.sock.sendall(bytes([0x06, 0x00, 0x10, 0x00, 0x07]))
            resp = self.sock.recv(1024)

            # Expected response: [0x06, count_l, count_h, ack_status, data... (64 bytes)]
            if len(resp) >= 68 and resp[0] == 0x06 and resp[1] == 0x10 and (resp[3] & 0x07) == 1:
                return resp[4:68]
            return None
        except Exception:
            return None

    def run(self):
        print(f"[Collector] Telemetry thread started. Target: {self.probe_ip}")

        sim_seq = 0
        sim_pos = 0.0
        sim_vel = 0.0
        sim_integral = 0.0
        sim_last_err = 0.0
        lfsr = 0x12345678

        last_retry_time = 0

        while g_running:
            # Check if user requested reconnect
            if g_force_reconnect.is_set():
                g_force_reconnect.clear()
                self.is_hw_connected = False
                if self.sock:
                    try: self.sock.close()
                    except: pass
                last_retry_time = 0

            # Try connecting hardware if not connected and not forced to sim
            if not self.is_hw_connected and not self.force_sim:
                now = time.time()
                if now - last_retry_time > 3.0: # Retry every 3 seconds
                    last_retry_time = now
                    if self.init_swd():
                        self.is_hw_connected = True
                        g_target_status["mode"] = "hardware_swd"
                        broadcast_sse({"type": "status", **g_target_status})
                    else:
                        g_target_status["mode"] = "simulation"
                        broadcast_sse({"type": "status", **g_target_status})

            # Read from Hardware or fallback to Simulation
            if self.is_hw_connected:
                raw = self.read_telemetry_block()
                if raw and len(raw) == 64:
                    pkt = parse_telemetry_packet(raw)
                    if pkt and pkt["magic"] == f"0x{TELEMETRY_MAGIC:08X}":
                        broadcast_sse(pkt)
                        continue
                # If read failed, revert to sim and attempt reconnect
                self.is_hw_connected = False
                if self.sock:
                    try: self.sock.close()
                    except: pass
                g_target_status["mode"] = "simulation"
                broadcast_sse({"type": "status", **g_target_status})

            # Simulation Generator (Runs matching STM32H7 firmware algorithm)
            sim_seq += 1
            dt = 0.001 # 1 ms
            t = sim_seq * dt

            # Multi-frequency waveforms
            sine = 100.0 * math.sin(2.0 * math.pi * 5.0 * t)
            tri_phase = (t * 2.0) % 1.0
            tri = (tri_phase * 200.0) if tri_phase < 0.5 else ((1.0 - tri_phase) * 200.0)
            sq = 60.0 if (t % 1.0) < 0.5 else 0.0

            # PID Simulation (Square setpoint 25 <-> 85)
            pid_sp = 85.0 if (t % 4.0) < 2.0 else 25.0
            err = pid_sp - sim_pos
            sim_integral += err * dt
            sim_integral = max(-50.0, min(50.0, sim_integral))
            deriv = (err - sim_last_err) / dt
            sim_last_err = err
            pid_out = 3.2 * err + 1.5 * sim_integral + 0.08 * deriv
            pid_out = max(-100.0, min(100.0, pid_out))

            # 2nd order mass-spring-damper plant
            accel = (pid_out * 8.0 - 6.0 * sim_vel - 10.0 * sim_pos) / 1.5
            sim_vel += accel * dt
            sim_pos += sim_vel * dt

            # Noise
            lfsr ^= (lfsr << 13) & 0xFFFFFFFF
            lfsr ^= (lfsr >> 17) & 0xFFFFFFFF
            lfsr ^= (lfsr << 5) & 0xFFFFFFFF
            noise = ((lfsr & 0xFFFF) / 32768.0 - 1.0) * 6.0

            pkt = {
                "type": "telemetry",
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
            }
            broadcast_sse(pkt)
            time.sleep(0.001) # ~1000 SPS streaming rate

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

            client_queue = queue.Queue(maxsize=200)
            with g_clients_lock:
                g_clients.append(client_queue)

            # Send initial status
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
                        # Keep-alive heartbeat
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
        # Silence standard HTTP request logging to keep console clean
        pass

def main():
    parser = argparse.ArgumentParser(description="MekProbe Real-time Visualizer & Performance Telemetry Bridge")
    parser.add_argument("--probe-ip", default=PROBE_IP, help="Wireless probe IP address (default: 192.168.4.1)")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Web dashboard port (default: 8080)")
    parser.add_argument("--sim", action="store_true", help="Force local simulation mode without connecting to probe")
    args = parser.parse_args()

    collector = TelemetryCollector(probe_ip=args.probe_ip, force_sim=args.sim)
    collector.start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), WebVisualizerHandler)
    print("\n" + "=" * 65)
    print(f"  MekProbe 60 FPS Telemetry Visualizer & Performance Analyzer")
    print("=" * 65)
    print(f"  [+] Web Dashboard UI:  http://localhost:{args.port}")
    print(f"  [+] Target Probe:      {args.probe_ip}:{PROBE_TCP_PORT}")
    print(f"  [+] Target Address:    0x{TARGET_ADDR:08X} (AXI SRAM RAM_D1)")
    print(f"  [+] Stream Endpoint:   http://localhost:{args.port}/events")
    print("=" * 65 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        global g_running
        g_running = False
        server.server_close()

if __name__ == "__main__":
    main()
