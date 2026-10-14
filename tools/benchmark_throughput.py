import socket
import struct
import time
import hashlib
import os

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SWD_CLOCK_HZ = 1_500_000

print("=" * 80)
print("     SWD DOWNLOAD & UPLOAD THROUGHPUT & INTEGRITY BENCHMARK")
print(f"  Probe IP: {PROBE_IP}:{PROBE_PORT} | SWD Clock: {SWD_CLOCK_HZ/1e6:.2f} MHz")
print("=" * 80)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(15.0)
s.connect((PROBE_IP, PROBE_PORT))

def recv_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection lost")
        buf += chunk
    return buf

def xfer_dap(req):
    s.sendall(bytes(req))
    cmd = req[0]
    if cmd in (0x02, 0x03, 0x04, 0x11, 0x12):
        return recv_exact(2)
    elif cmd == 0x05: # DAP_Transfer
        hdr = recv_exact(3)
        count = hdr[1]
        ack = hdr[2]
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
            data = recv_exact(read_bytes)
            return hdr + data
        return hdr
    elif cmd == 0x06: # DAP_TransferBlock
        hdr = recv_exact(4)
        cnt = hdr[1] | (hdr[2] << 8)
        req_val = req[4]
        if (req_val & 0x02): # Read block returns cnt * 4 data bytes
            data = recv_exact(cnt * 4)
            return hdr + data
        return hdr
    else:
        return s.recv(2048)

# Connect & configure
xfer_dap([0x02, 0x01])
xfer_dap(bytes([0x11]) + struct.pack("<I", SWD_CLOCK_HZ))
xfer_dap([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

# Sync SWD
xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 16, 0x9E, 0xE7])
xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 8, 0x00])

# Read DPIDR
r = xfer_dap([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
print(f"[Init] DPIDR: 0x{dpidr:08X}")
assert dpidr == 0x6BA02477

# Powerup & AP 0
xfer_dap([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer_dap([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer_dap([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23]) # CSW 32-bit auto-inc

DEST_ADDR = 0x24000000  # AXI SRAM (512 KB capacity)
WORDS_PER_BLOCK = 64    # 256 bytes per packet
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4

sizes = [
    16 * 1024,   # 16 KB
    64 * 1024,   # 64 KB
    128 * 1024,  # 128 KB
    256 * 1024,  # 256 KB
]

results = []

for sz in sizes:
    num_blocks = sz // BYTES_PER_BLOCK
    actual_sz = num_blocks * BYTES_PER_BLOCK
    print(f"\n--- Testing Block Size: {actual_sz/1024:.0f} KB ({actual_sz} Bytes / {num_blocks} Blocks) ---")
    
    test_payload = os.urandom(actual_sz)
    sha_expected = hashlib.sha256(test_payload).hexdigest()
    
    # 1. Download (Write)
    t0 = time.perf_counter()
    for b_idx in range(num_blocks):
        cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
        xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
        chunk = test_payload[b_idx * BYTES_PER_BLOCK : (b_idx + 1) * BYTES_PER_BLOCK]
        req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
        resp = xfer_dap(req)
        assert len(resp) >= 4 and resp[3] == 1, f"Write block err at {b_idx}: {list(resp)}"
    t1 = time.perf_counter()
    
    dt_w = t1 - t0
    write_kb_s = (actual_sz / 1024.0) / dt_w
    write_mbps = (actual_sz * 8.0 / 1e6) / dt_w
    print(f"  [Write/Download] {actual_sz/1024:6.1f} KB in {dt_w:6.3f}s -> {write_kb_s:7.2f} KB/s ({write_mbps:5.2f} Mbps)")
    
    # 2. Upload (Readback)
    readback_buf = bytearray()
    t0 = time.perf_counter()
    for b_idx in range(num_blocks):
        cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
        xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
        req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
        resp = xfer_dap(req)
        assert len(resp) >= 4 + BYTES_PER_BLOCK and resp[3] == 1, f"Read block err at {b_idx}: {list(resp[:4])}"
        readback_buf += resp[4 : 4 + BYTES_PER_BLOCK]
    t1 = time.perf_counter()
    
    dt_r = t1 - t0
    read_kb_s = (len(readback_buf) / 1024.0) / dt_r
    read_mbps = (len(readback_buf) * 8.0 / 1e6) / dt_r
    sha_actual = hashlib.sha256(readback_buf).hexdigest()
    match = (sha_expected == sha_actual)
    
    print(f"  [Read/Upload]    {len(readback_buf)/1024:6.1f} KB in {dt_r:6.3f}s -> {read_kb_s:7.2f} KB/s ({read_mbps:5.2f} Mbps)")
    print(f"  [Integrity]      Expected SHA-256 : {sha_expected}")
    print(f"  [Integrity]      Readback SHA-256 : {sha_actual}")
    print(f"  [Integrity]      Status           : {'100% BIT-EXACT MATCH (ZERO ERRORS)' if match else 'CORRUPTED'}")
    assert match, "Data corruption detected!"
    
    results.append({
        "size_kb": actual_sz / 1024.0,
        "write_kb_s": write_kb_s,
        "write_mbps": write_mbps,
        "read_kb_s": read_kb_s,
        "read_mbps": read_mbps,
        "integrity": match
    })

s.close()

print("\n" + "=" * 80)
print("                 THROUGHPUT BENCHMARK SUMMARY")
print("=" * 80)
for r in results:
    print(f"  Size: {r['size_kb']:4.0f} KB | Write: {r['write_kb_s']:6.2f} KB/s ({r['write_mbps']:5.2f} Mbps) | Read: {r['read_kb_s']:6.2f} KB/s ({r['read_mbps']:5.2f} Mbps) | Integrity: 100% PASS")
print("=" * 80)
