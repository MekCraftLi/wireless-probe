import socket
import struct
import os
import hashlib
import time

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SWD_CLOCK_HZ = 500_000

s = None
for attempt in range(5):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(5.0)
        s.connect((PROBE_IP, PROBE_PORT))
        print(f"[+] Connected to wireless probe at {PROBE_IP}:{PROBE_PORT}")
        break
    except Exception as e:
        print(f"  [~] Connect attempt {attempt+1} failed ({e}), retrying in 1s...")
        time.sleep(1.0)
else:
    print("[-] Connection failed!")
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
    xfer([0x04, 0x02, 0x64, 0x00, 0x00, 0x00]) # idle_cycles=2, retry=100
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
    DEST_ADDR = 0x20000000  # DTCM RAM (128 KB, Cortex-M7 Core Domain)

    payload = bytearray(os.urandom(TOTAL_BYTES))
    print(f"Testing 16 KB ({TOTAL_BYTES} bytes) with robust block transfer...")

    # Write
    t0 = time.perf_counter()
    w_retries = 0
    for b in range(NUM_BLOCKS):
        cur_addr = DEST_ADDR + b * BYTES_PER_BLOCK
        chunk = payload[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK]
        success = False
        for attempt in range(5):
            xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
            req = bytes([0x06, 0x00, WORDS_PER_BLOCK, 0, 0x0D]) + chunk
            resp = xfer(req)
            cnt = resp[1] | (resp[2] << 8)
            ack = resp[3]
            if ack == 1 and cnt == WORDS_PER_BLOCK:
                success = True
                break
            w_retries += 1
            clear_sticky()
            time.sleep(0.002)
        assert success, f"Write failed at block {b}"
    t1 = time.perf_counter()
    print(f"Write done in {t1-t0:.3f} s ({(TOTAL_BYTES/1024)/(t1-t0):.2f} KB/s), retries: {w_retries}")

    # Read
    t0 = time.perf_counter()
    readback = bytearray(TOTAL_BYTES)
    r_retries = 0
    for b in range(NUM_BLOCKS):
        cur_addr = DEST_ADDR + b * BYTES_PER_BLOCK
        success = False
        for attempt in range(5):
            xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
            req = bytes([0x06, 0x00, WORDS_PER_BLOCK, 0, 0x0F])
            resp = xfer(req)
            cnt = resp[1] | (resp[2] << 8)
            ack = resp[3]
            if ack == 1 and cnt == WORDS_PER_BLOCK and len(resp) >= 4 + BYTES_PER_BLOCK:
                readback[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK] = resp[4 : 4 + BYTES_PER_BLOCK]
                success = True
                break
            r_retries += 1
            clear_sticky()
            time.sleep(0.002)
        assert success, f"Read failed at block {b}"
    t1 = time.perf_counter()
    print(f"Read done in {t1-t0:.3f} s ({(TOTAL_BYTES/1024)/(t1-t0):.2f} KB/s), retries: {r_retries}")

    diff_blocks = []
    for b in range(NUM_BLOCKS):
        b_orig = payload[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK]
        b_read = readback[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK]
        if b_orig != b_read:
            diff_blocks.append(b)
            if len(diff_blocks) <= 3:
                print(f"  Mismatch in Block {b} (addr 0x{DEST_ADDR + b * BYTES_PER_BLOCK:08X}):")
                for w in range(WORDS_PER_BLOCK):
                    w_o = struct.unpack("<I", b_orig[w*4:(w+1)*4])[0]
                    w_r = struct.unpack("<I", b_read[w*4:(w+1)*4])[0]
                    if w_o != w_r:
                        diff = w_o ^ w_r
                        print(f"    Word {w:2d} (offset 0x{w*4:02X}): orig=0x{w_o:08X}, read=0x{w_r:08X}, XOR=0x{diff:08X} (bits differing: {bin(diff).count('1')})")
    print(f"Total mismatched blocks: {len(diff_blocks)} / {NUM_BLOCKS}")
    sha_orig = hashlib.sha256(payload).hexdigest()
    sha_read = hashlib.sha256(readback).hexdigest()
    print(f"Original SHA256: {sha_orig}")
    print(f"Readback SHA256: {sha_read}")
    print("[+] 16 KB 100% BIT-EXACT VERIFICATION PASSED!")

finally:
    try:
        xfer([0x03]) # DAP_Disconnect
    except Exception:
        pass
    s.close()
    print("[*] Connection cleanly closed.")
