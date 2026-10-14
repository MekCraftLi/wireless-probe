import socket
import struct
import os
import hashlib
import time

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SWD_CLOCK_HZ = 1_000_000

s = None
for attempt in range(5):
    try:
        s = socket.socket()
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(5.0)
        s.connect((PROBE_IP, PROBE_PORT))
        print(f"[+] Connected to wireless probe at {PROBE_IP}:{PROBE_PORT}")
        break
    except Exception:
        time.sleep(1.0)
else:
    print("Connection failed!")
    exit(1)

try:
    def recv_exact(n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                raise TimeoutError(f"Socket timed out waiting for {n - len(buf)} bytes")
            if not chunk: raise ConnectionError("Socket closed")
            buf += chunk
        return buf

    def xfer(req):
        s.sendall(bytes(req))
        cmd = req[0]
        if cmd in (0x02, 0x03, 0x04, 0x11, 0x12):
            return recv_exact(2)
        elif cmd == 0x05:
            hdr = recv_exact(3)
            cnt = hdr[1]
            rb = 0
            idx = 3
            for _ in range(cnt):
                if req[idx] & 2:
                    rb += 4
                    idx += 1
                else:
                    idx += 5
            return hdr + recv_exact(rb) if rb else hdr
        elif cmd == 0x06:
            hdr = recv_exact(4)
            cnt = hdr[1] | (hdr[2] << 8)
            return hdr + recv_exact(cnt * 4) if (req[4] & 2) else hdr

    def clear_sticky():
        xfer([0x05, 0x00, 0x01, 0x00, 0x1E, 0x00, 0x00, 0x00])

    xfer([0x02, 0x01])
    xfer(bytes([0x11]) + struct.pack("<I", SWD_CLOCK_HZ))
    xfer([0x04, 0x02, 0x64, 0x00, 0x00, 0x00])
    xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    xfer([0x12, 16, 0x9E, 0xE7])
    xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    xfer([0x12, 8, 0x00])
    xfer([0x05, 0x00, 0x01, 0x02])
    xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
    xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
    xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

    BYTES_PER_BLOCK = 256
    WORDS_PER_BLOCK = 64
    NUM_BLOCKS = 64
    TOTAL_BYTES = NUM_BLOCKS * BYTES_PER_BLOCK
    DEST_ADDR = 0x20000000  # DTCM RAM

    payload = bytearray(os.urandom(TOTAL_BYTES))
    print(f"Testing 16 KB ({TOTAL_BYTES} bytes) with write-verify loop...")

    t0 = time.perf_counter()
    retries_count = 0
    readback = bytearray(TOTAL_BYTES)

    for b in range(NUM_BLOCKS):
        cur_addr = DEST_ADDR + b * BYTES_PER_BLOCK
        chunk = payload[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK]
        
        # Write and readback block
        for attempt in range(5):
            xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
            req_w = bytes([0x06, 0x00, WORDS_PER_BLOCK, 0, 0x0D]) + chunk
            resp_w = xfer(req_w)
            
            xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
            req_r = bytes([0x06, 0x00, WORDS_PER_BLOCK, 0, 0x0F])
            resp_r = xfer(req_r)
            rb = resp_r[4 : 4 + BYTES_PER_BLOCK]
            
            if rb == chunk:
                readback[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK] = rb
                break
            else:
                print(f"  Attempt {attempt}: resp_w={list(resp_w[:4])}, resp_r_len={len(resp_r)}, resp_r_hdr={list(resp_r[:4])}")
                for w in range(min(4, WORDS_PER_BLOCK)):
                    w_o = struct.unpack("<I", chunk[w*4:(w+1)*4])[0]
                    w_r = struct.unpack("<I", rb[w*4:(w+1)*4])[0] if len(rb) >= (w+1)*4 else 0
                    print(f"    Word {w}: exp=0x{w_o:08X}, got=0x{w_r:08X} {'*' if w_o != w_r else ''}")
            retries_count += 1
            clear_sticky()
            time.sleep(0.01)
        else:
            raise RuntimeError(f"Block {b} failed after 5 attempts!")

    t1 = time.perf_counter()
    dt = t1 - t0
    rate_kb = (TOTAL_BYTES / 1024.0) / dt
    
    sha_orig = hashlib.sha256(payload).hexdigest()
    sha_read = hashlib.sha256(readback).hexdigest()
    print(f"Completed in {dt:.3f} s (Throughput: {rate_kb:.2f} KB/s, Total retries: {retries_count})")
    print(f"Original SHA256: {sha_orig}")
    print(f"Readback SHA256: {sha_read}")
    match = (sha_orig == sha_read)
    print(f"Result: {'100% BIT-EXACT MATCH!' if match else 'MISMATCH'}")
    assert match

finally:
    try:
        xfer([0x03])
    except Exception:
        pass
    s.close()
    print("[*] Connection cleanly closed.")
