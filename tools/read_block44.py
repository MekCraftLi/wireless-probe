import socket
import struct

s = socket.socket()
s.connect(("192.168.4.1", 3240))

def recv_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk: raise ConnectionError()
        buf += chunk
    return buf

def xfer(b):
    s.sendall(bytes(b))
    return s.recv(1024)

xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", 1000000))
xfer([0x04, 0x01, 0x32, 0, 0, 0])
xfer([0x12, 56] + [0xFF]*7 + [0x12, 16, 0x9E, 0xE7, 0x12, 56] + [0xFF]*7 + [0x12, 8, 0])
xfer([0x05, 0, 4, 2, 4, 0, 0, 0, 0x50, 8, 0, 0, 0, 0, 1, 0x52, 0, 0, 0x23])

addr = 0x24000000 + 44 * 256
xfer([0x05, 0, 1, 5] + list(struct.pack("<I", addr)))
s.sendall(bytes([0x06, 0, 64, 0, 0x0F]))
resp = recv_exact(4 + 256)
print("Block 44 resp len:", len(resp), "hdr:", list(resp[:4]))
print("Words [0:10]:", [hex(struct.unpack("<I", resp[4+i*4:8+i*4])[0]) for i in range(10)])
print("Words [10:20]:", [hex(struct.unpack("<I", resp[4+i*4:8+i*4])[0]) for i in range(10, 20)])
s.close()
