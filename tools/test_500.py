import socket
import struct

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(5.0)
s.connect(("192.168.4.1", 3240))

def xfer(req):
    s.sendall(bytes(req))
    resp = s.recv(2048)
    return resp

xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", 2500000))
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])

# Read DPIDR (MANDATORY after SWD line reset)
r = xfer([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
print(f"DPIDR: 0x{dpidr:08X}")

xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

req_batch = [0x05, 0x00, 9]
for addr in [0xE000ED00, 0xE000ED04, 0xE000ED08, 0xE000ED0C]:
    tar_b = list(struct.pack("<I", addr))
    req_batch += [0x05] + tar_b + [0x0F]
req_batch += [0x0E]

err_count = 0
for i in range(500):
    r = xfer(req_batch)
    if len(r) != 23 or r[2] != 1:
        print(f"Cycle {i} failed: len={len(r)}, resp={list(r)}")
        err_count += 1
        break

print(f"500 cycles done. Errors: {err_count}")

# Check DP status
r = xfer([0x05, 0x00, 0x01, 0x06])
print("DP CTRL/STAT:", list(r))

# Set TAR
r = xfer([0x05, 0x00, 0x01, 0x05, 0x00, 0x00, 0x00, 0x24])
print("Set TAR resp:", list(r))

# Write block
WORDS_PER_BLOCK = 64
payload = b"\x55" * (WORDS_PER_BLOCK * 4)
req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + payload
resp = xfer(req)
print("Write block resp:", list(resp))

s.close()
