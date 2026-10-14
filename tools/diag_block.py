import socket
import struct
import os

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

# Test single block write and readback
data = bytearray(os.urandom(256))
xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", 0x24000000)))
r_w = xfer(bytes([0x06, 0x00, 64, 0, 0x0D]) + data)
print("Write resp:", list(r_w))

xfer([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", 0x24000000)))
r_r = xfer(bytes([0x06, 0x00, 64, 0, 0x0F]))
print("Read resp hdr:", list(r_r[:4]))
read_data = r_r[4:]
print(f"Data len: {len(data)}, Read len: {len(read_data)}")
print("Match:", read_data == data)
if read_data != data:
    for i in range(len(data)):
        if data[i] != read_data[i]:
            print(f"First mismatch at byte {i}: expected 0x{data[i]:02X}, got 0x{read_data[i]:02X}")
            break
    print("Sent[0:16] :", [hex(b) for b in data[:16]])
    print("Recv[0:16] :", [hex(b) for b in read_data[:16]])
    print("Sent[-16:] :", [hex(b) for b in data[-16:]])
    print("Recv[-16:] :", [hex(b) for b in read_data[-16:]])

s.close()
