import socket
import struct
import os
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
        break
    except Exception:
        time.sleep(1.0)
else:
    print("Connection failed!")
    exit(1)

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
xfer([0x04, 0x02, 0x64, 0x00, 0x00, 0x00])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])
xfer([0x05, 0x00, 0x01, 0x02])
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# Test writing and reading 256 bytes with small delay before write
mismatches = 0
for i in range(10):
    payload = bytearray(os.urandom(256))
    addr = 0x20000000
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", addr)))
    time.sleep(0.005) # small pause to ensure previous response is completely settled
    req_w = bytes([0x06, 0x00, 64, 0, 0x0D]) + payload
    resp_w = xfer(req_w)
    
    time.sleep(0.005)
    xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", addr)))
    time.sleep(0.005)
    req_r = bytes([0x06, 0x00, 64, 0, 0x0F])
    resp_r = xfer(req_r)
    rb = resp_r[4:4+256]
    match = (rb == payload)
    if not match:
        mismatches += 1
        print(f"Iter {i} MISMATCH!")
        for w in range(64):
            w_o = struct.unpack("<I", payload[w*4:(w+1)*4])[0]
            w_r = struct.unpack("<I", rb[w*4:(w+1)*4])[0]
            if w_o != w_r:
                print(f"  First mismatch at word {w}: orig=0x{w_o:08X}, read=0x{w_r:08X}")
                break
    else:
        print(f"Iter {i}: 100% BIT MATCH!")

print(f"Total: {10 - mismatches}/10 matched.")
s.close()
