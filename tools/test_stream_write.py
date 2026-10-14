import socket
import struct
import time
import hashlib
import os

PROBE_IP = "192.168.4.1"
PROBE_PORT = 3240

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(5.0)
s.connect((PROBE_IP, PROBE_PORT))

def xfer(req):
    s.sendall(bytes(req))
    resp = s.recv(2048)
    return resp

# 1. Connect & configure
xfer([0x02, 0x01]) # Connect
xfer(bytes([0x11]) + struct.pack("<I", 2500000)) # 2.5MHz SWD clock
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

# Sync SWD
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])

r = xfer([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0]
print(f"DPIDR: 0x{dpidr:08X}")

# Setup AP 0, CSW
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50]) # PWRUP
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00]) # AP 0
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23]) # CSW 32-bit auto-inc

DEST_ADDR = 0x24000000 # AXI SRAM
WORDS_PER_BLOCK = 64   # 256 bytes
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4

# Test writing 1KB (4 blocks of 256 bytes)
TOTAL_BYTES = 1024
num_blocks = TOTAL_BYTES // BYTES_PER_BLOCK
test_pattern = bytearray()
for i in range(TOTAL_BYTES):
    test_pattern.append((i * 3 + 7) & 0xFF)

print(f"Writing {TOTAL_BYTES} bytes ({num_blocks} blocks)...")
for i in range(num_blocks):
    cur_addr = DEST_ADDR + i * BYTES_PER_BLOCK
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    chunk = test_pattern[i * BYTES_PER_BLOCK : (i + 1) * BYTES_PER_BLOCK]
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
    resp = xfer(req)
    print(f"Write block {i} resp: {list(resp)}")

# Test reading back
readback = bytearray()
print(f"Reading back {TOTAL_BYTES} bytes...")
for i in range(num_blocks):
    cur_addr = DEST_ADDR + i * BYTES_PER_BLOCK
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
    resp = xfer(req)
    print(f"Read block {i} resp len: {len(resp)}, header: {list(resp[:4])}")
    readback += resp[4 : 4 + BYTES_PER_BLOCK]

print("\n--- Comparison ---")
print("First 32 bytes written :", [f"{b:02X}" for b in test_pattern[:32]])
print("First 32 bytes read    :", [f"{b:02X}" for b in readback[:32]])
print("Match:", test_pattern == readback)

if test_pattern != readback:
    for idx, (b1, b2) in enumerate(zip(test_pattern, readback)):
        if b1 != b2:
            print(f"First mismatch at byte {idx}: expected 0x{b1:02X}, got 0x{b2:02X}")
            break

s.close()
