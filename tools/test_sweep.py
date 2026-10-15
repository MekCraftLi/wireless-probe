import socket
import struct

s = socket.socket()
s.connect(('192.168.4.1', 3240))
s.sendall(bytes([0x02, 0x01]))
s.recv(64)

for f in [500000, 600000, 700000, 750000, 800000, 900000, 1000000]:
    s.sendall(bytes([0x11]) + struct.pack('<I', f))
    s.recv(64)
    s.sendall(bytes([0x04, 0, 50, 0, 0, 0]))
    s.recv(64)
    s.sendall(bytes([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    s.recv(64)
    s.sendall(bytes([0x12, 16, 0x9E, 0xE7]))
    s.recv(64)
    s.sendall(bytes([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    s.recv(64)
    s.sendall(bytes([0x12, 8, 0x00]))
    s.recv(64)
    s.sendall(bytes([0x05, 0, 1, 2]))
    resp = list(s.recv(64))
    if len(resp) >= 7 and resp[1] == 1 and (resp[2] & 0x07) == 1:
        dpidr = struct.unpack("<I", bytes(resp[3:7]))[0]
        print(f"Freq {f//1000} kHz: PASS (DPIDR=0x{dpidr:08X})")
    else:
        print(f"Freq {f//1000} kHz: FAIL (Resp={resp})")

s.sendall(bytes([0x03]))
s.close()
