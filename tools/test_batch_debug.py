import socket
import struct
import time

def create_connection():
    for attempt in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3.0)
            sock.connect(("192.168.4.1", 3240))
            return sock
        except Exception:
            time.sleep(1.0)
    raise ConnectionError("Failed")

sock = create_connection()

def xfer(req):
    sock.sendall(bytes(req))
    time.sleep(0.02)
    return sock.recv(2048)

xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", 2500000))
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])
xfer([0x05, 0x00, 0x01, 0x02])
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

for n in [5, 6, 7, 8, 10, 12, 16]:
    addrs = [0x20000000 + i * 0x40 for i in range(n)]
    req = [0x05, 0x00, 2 * n]
    for a in addrs:
        req += [0x05] + list(struct.pack("<I", a)) + [0x0F]
    
    resp = xfer(req)
    exp_len = 3 + 4 * n
    match = (len(resp) == exp_len and resp[2] == 1 and resp[1] == 2 * n)
    print(f"N={n:2d}: count={resp[1] if len(resp)>=2 else 0:2d}/{2*n:2d}, ACK={resp[2] if len(resp)>=3 else 0}, len={len(resp):2d}/{exp_len:2d} -> {match}")

sock.close()
