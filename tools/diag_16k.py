import socket
import struct
import os
import hashlib

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240
SWD_CLOCK_HZ = 1_000_000

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(10.0)
s.connect((PROBE_IP, PROBE_PORT))

def recv_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk: raise ConnectionError()
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

xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", SWD_CLOCK_HZ))
xfer([0x04, 0x01, 0x32, 0x00, 0x00, 0x00])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])
xfer([0x05, 0x00, 0x01, 0x02])
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

WORDS_PER_BLOCK = 64
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4
NUM_BLOCKS = 64
TOTAL_BYTES = NUM_BLOCKS * BYTES_PER_BLOCK
DEST_ADDR = 0x24000000

payload = bytearray(os.urandom(TOTAL_BYTES))
print(f"Testing {TOTAL_BYTES} bytes ({NUM_BLOCKS} blocks)...")

# Write
for b in range(NUM_BLOCKS):
    cur_addr = DEST_ADDR + b * BYTES_PER_BLOCK
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    chunk = payload[b * BYTES_PER_BLOCK : (b + 1) * BYTES_PER_BLOCK]
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
    resp = xfer(req)
    if resp[3] != 1:
        print(f"Write error at block {b}: {list(resp)}")

# Read
readback = bytearray()
for b in range(NUM_BLOCKS):
    cur_addr = DEST_ADDR + b * BYTES_PER_BLOCK
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
    resp = xfer(req)
    if resp[3] != 1:
        print(f"Read error at block {b}: {list(resp[:4])}")
    readback += resp[4 : 4 + BYTES_PER_BLOCK]

print(f"Len payload: {len(payload)}, Len readback: {len(readback)}")
print("SHA256 Payload :", hashlib.sha256(payload).hexdigest())
print("SHA256 Readback:", hashlib.sha256(readback).hexdigest())

diffs = []
for i in range(TOTAL_BYTES):
    if payload[i] != readback[i]:
        diffs.append(i)

print(f"Total byte mismatches: {len(diffs)}")
if diffs:
    print(f"First 10 mismatch indices: {diffs[:10]}")
    for idx in diffs[:5]:
        b = idx // BYTES_PER_BLOCK
        off = idx % BYTES_PER_BLOCK
        print(f"  Byte {idx} (Block {b}, Offset {off}): expected 0x{payload[idx]:02X}, got 0x{readback[idx]:02X}")

s.close()
