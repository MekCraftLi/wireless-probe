import socket
import struct
import time

def create_connection():
    for attempt in range(5):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(5.0)
            sock.connect(("192.168.4.1", 3240))
            return sock
        except Exception as e:
            time.sleep(1.0)
    raise ConnectionError("Failed to connect")

s = create_connection()

def xfer(req):
    s.sendall(bytes(req))
    return s.recv(2048)

xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", 2500000))
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])

# DPIDR
xfer([0x05, 0x00, 0x01, 0x02])

# Powerup & AP 0
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# Clean 8-transfer batched read of 4 registers
addrs = [0xE000ED00, 0xE000ED04, 0xE000ED08, 0xE000ED0C]
req_batch = [0x05, 0x00, 8]
for addr in addrs:
    tar_b = list(struct.pack("<I", addr))
    req_batch += [0x05] + tar_b + [0x0F]

resp = xfer(req_batch)
print(f"Batched resp len: {len(resp)}, raw: {[hex(b) for b in resp]}")
if len(resp) >= 3:
    count = resp[1]
    ack = resp[2]
    print(f"Count: {count}, ACK: {ack}")
    data = resp[3:]
    for i in range(len(data) // 4):
        val = struct.unpack("<I", data[i*4:(i+1)*4])[0]
        print(f"  Var[{i}] (0x{addrs[i]:08X}): 0x{val:08X}")

s.close()
