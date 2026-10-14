import socket
import struct
import time

s = None
for attempt in range(5):
    try:
        s = socket.socket()
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(5.0)
        s.connect(("192.168.4.1", 3240))
        break
    except Exception:
        time.sleep(1.0)
else:
    print("Connection failed!")
    exit(1)

def recv_exact(n):
    b = bytearray()
    while len(b) < n:
        chunk = s.recv(n - len(b))
        if not chunk: raise ConnectionError()
        b += chunk
    return b

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

xfer([0x02, 1])
xfer(bytes([0x11]) + struct.pack("<I", 1000000))
xfer([0x04, 2, 0x64, 0, 0, 0])

# Read Block 9 at 0x20000900
xfer([0x05, 0, 1, 5] + list(struct.pack("<I", 0x20000900)))
resp = xfer(bytes([0x06, 0, 64, 0, 0x0F]))
print("Block 9 hdr:", list(resp[:4]))
for w in range(20, 26):
    val = struct.unpack("<I", resp[4+w*4:8+w*4])[0]
    print(f"  Word {w} (offset 0x{w*4:02X}): 0x{val:08X}")

# Now read each word individually via DAP_Transfer (single reads) to verify if RAM or Block transfer
print("Reading individually via DAP_Transfer:")
for w in range(20, 26):
    addr = 0x20000900 + w * 4
    tar_b = list(struct.pack("<I", addr))
    r = xfer([0x05, 0x00, 0x02, 0x05] + tar_b + [0x0F])
    val = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
    print(f"  Word {w} (addr 0x{addr:08X}) -> 0x{val:08X} [ACK={r[2]}]")

s.close()
