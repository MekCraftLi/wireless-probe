import socket
import struct

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.connect(("192.168.4.1", 3240))

def xfer(req):
    s.sendall(bytes(req))
    return s.recv(2048)

# Read DP CTRL/STAT (DP reg 0x04, RnW=1 -> req=0x06)
r = xfer([0x05, 0x00, 0x01, 0x06])
print("DP CTRL/STAT raw:", list(r))
if len(r) >= 7:
    val = struct.unpack("<I", r[3:7])[0]
    print(f"CTRL/STAT: 0x{val:08X}")
    print(f"  CSYSPWRUPACK: {(val >> 31) & 1}")
    print(f"  CDBGPWRUPACK: {(val >> 29) & 1}")
    print(f"  STICKYERR   : {(val >> 5) & 1}")
    print(f"  STICKYCMP   : {(val >> 4) & 1}")
    print(f"  STICKYORUN  : {(val >> 1) & 1}")

s.close()
