import socket
import struct
import time
import hashlib
import os

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(5.0)
s.connect(("192.168.4.1", 3240))

def recv_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed prematurely")
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
        # Count read requests in req
        req_cnt = req[2]
        # Inspect req to see how many reads
        idx = 3
        read_words = 0
        for _ in range(req_cnt):
            val = req[idx]
            idx += 1
            if val & 0x02: # RnW
                read_words += 1
            else:
                idx += 4 # Write payload
        if ack == 1 and read_words > 0:
            data = recv_exact(read_words * 4)
            return hdr + data
        return hdr
    elif cmd == 0x06: # DAP_TransferBlock
        hdr = recv_exact(4)
        cnt = hdr[1] | (hdr[2] << 8)
        ack = hdr[3]
        req_val = req[4]
        if (req_val & 0x02) and ack == 1: # Read block
            data = recv_exact(cnt * 4)
            return hdr + data
        return hdr
    else:
        return s.recv(2048)

# 1. Connect & Configure
xfer_dap([0x02, 0x01])
xfer_dap(bytes([0x11]) + struct.pack("<I", 2500000))
xfer_dap([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

# 2. SWD Line Reset
xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 16, 0x9E, 0xE7])
xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 8, 0x00])

# 3. Read DPIDR (mandatory)
r = xfer_dap([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
print(f"[Init] DPIDR: 0x{dpidr:08X}")
assert dpidr == 0x6BA02477

# 4. Powerup & AP 0 setup
xfer_dap([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer_dap([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer_dap([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# 5. Benchmark 1A: Single-access test (100 cycles = 1000 accesses)
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

errs_1a = 0
t0 = time.perf_counter()
for _ in range(100):
    for addr, _ in non_contig_addrs:
        tar_b = list(struct.pack("<I", addr))
        r = xfer_dap([0x05, 0x00, 0x02, 0x05] + tar_b + [0x0F])
        if len(r) != 7 or r[1] != 2 or r[2] != 1:
            errs_1a += 1
t1 = time.perf_counter()
dt_1a = t1 - t0
rate_1a = 1000 / dt_1a
print(f"[1A] 1000 accesses in {dt_1a:.3f}s -> {rate_1a:.2f} accesses/s (Errs={errs_1a})")
assert errs_1a == 0

# 6. Benchmark 1B: Batched access (200 cycles of 4 registers = 800 accesses)
addrs_4 = non_contig_addrs[:4]
req_batch = [0x05, 0x00, 8]
for addr, _ in addrs_4:
    tar_b = list(struct.pack("<I", addr))
    req_batch += [0x05] + tar_b + [0x0F]

errs_1b = 0
t0 = time.perf_counter()
for i in range(200):
    r = xfer_dap(req_batch)
    if len(r) != 19 or r[1] != 8 or r[2] != 1:
        if errs_1b == 0:
            print(f"First 1B error at cycle {i}: len={len(r)}, raw={[hex(b) for b in r]}")
        errs_1b += 1
t1 = time.perf_counter()
dt_1b = t1 - t0
rate_1b = (200 * 4) / dt_1b
print(f"[1B] 800 batched accesses in {dt_1b:.3f}s -> {rate_1b:.2f} accesses/s (Errs={errs_1b})")
assert errs_1b == 0

# 7. Benchmark 2 & 3: 16KB Stream Write & Readback Integrity Check
DEST_ADDR = 0x24000000
WORDS_PER_BLOCK = 64
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4
TOTAL_BYTES = 16 * 1024
num_blocks = TOTAL_BYTES // BYTES_PER_BLOCK
test_payload = os.urandom(TOTAL_BYTES)
sha_expected = hashlib.sha256(test_payload).hexdigest()

# Write 16KB
t0 = time.perf_counter()
for i in range(num_blocks):
    cur_addr = DEST_ADDR + i * BYTES_PER_BLOCK
    xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    chunk = test_payload[i * BYTES_PER_BLOCK : (i + 1) * BYTES_PER_BLOCK]
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
    resp = xfer_dap(req)
    assert len(resp) >= 4 and resp[3] == 1, f"Write block err at {i}: {list(resp)}"
t1 = time.perf_counter()
dt_w = t1 - t0
write_kb_s = (TOTAL_BYTES / 1024.0) / dt_w
print(f"[2] Write 16KB in {dt_w:.3f}s -> {write_kb_s:.2f} KB/s ({write_kb_s*8/1000:.2f} Mbps)")

# Read 16KB
readback = bytearray()
t0 = time.perf_counter()
for i in range(num_blocks):
    cur_addr = DEST_ADDR + i * BYTES_PER_BLOCK
    xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
    resp = xfer_dap(req)
    assert len(resp) >= 4 + BYTES_PER_BLOCK and resp[3] == 1, f"Read block err at {i}: {list(resp[:4])}"
    readback += resp[4 : 4 + BYTES_PER_BLOCK]
t1 = time.perf_counter()
dt_r = t1 - t0
read_kb_s = (len(readback) / 1024.0) / dt_r
sha_actual = hashlib.sha256(readback).hexdigest()
print(f"[3] Read 16KB in {dt_r:.3f}s -> {read_kb_s:.2f} KB/s ({read_kb_s*8/1000:.2f} Mbps)")
print(f"    SHA Match: {sha_expected == sha_actual} ({sha_actual[:16]}...)")
assert sha_expected == sha_actual

s.close()
print("\n>>> ALL TESTS PASSED WITH 100% BIT-EXACT INTEGRITY! <<<")
