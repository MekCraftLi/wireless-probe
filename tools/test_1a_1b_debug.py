import socket
import struct
import time

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
s.settimeout(5.0)
s.connect(("192.168.4.1", 3240))

def xfer(req):
    s.sendall(bytes(req))
    resp = s.recv(2048)
    return resp

# Phase 0
xfer([0x02, 0x01])
xfer(bytes([0x11]) + struct.pack("<I", 2500000))
xfer([0x04, 0x00, 0x32, 0x00, 0x00, 0x00])

# Sync SWD
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 16, 0x9E, 0xE7])
xfer([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer([0x12, 8, 0x00])

# DPIDR
r = xfer([0x05, 0x00, 0x01, 0x02])
dpidr = struct.unpack("<I", r[3:7])[0] if len(r) >= 7 else 0
print(f"Phase 0 DPIDR: 0x{dpidr:08X}")

# Power up
xfer([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# Run 1A: 100 cycles of 10 addresses
non_contig_addrs = [
    (0xE000ED00, "CPUID"),
    (0xE000ED04, "ICSR"),
    (0xE000ED08, "VTOR"),
    (0xE000ED0C, "AIRCR"),
    (0x58024400, "DBGMCU_IDC"),
    (0x58000400, "RCC_CR"),
    (0x58000410, "RCC_CFGR"),
    (0x58020000, "GPIOA_MODER"),
    (0x24000000, "AXI_SRAM"),
    (0x20000000, "DTCM"),
]

errs_1a = 0
for cyc in range(100):
    for addr, name in non_contig_addrs:
        tar_b = list(struct.pack("<I", addr))
        r = xfer([0x05, 0x00, 0x03, 0x05] + tar_b + [0x0F, 0x0E])
        if len(r) < 7 or r[2] != 1:
            errs_1a += 1
            print(f"1A err at cyc {cyc}, {name}: {list(r)}")
            break
    if errs_1a: break

print(f"1A completed with {errs_1a} errors.")

# Check DP status after 1A
r = xfer([0x05, 0x00, 0x01, 0x06])
print("DP CTRL/STAT after 1A:", list(r))

# Run 1B: 500 cycles of batched 4 addresses
addrs_4 = non_contig_addrs[:4]
req_batch = [0x05, 0x00, 9]
for addr, _ in addrs_4:
    tar_b = list(struct.pack("<I", addr))
    req_batch += [0x05] + tar_b + [0x0F]
req_batch += [0x0E]

errs_1b = 0
for cyc in range(500):
    r = xfer(req_batch)
    if len(r) != 23 or r[2] != 1:
        errs_1b += 1
        print(f"1B err at cyc {cyc}: len={len(r)}, resp={list(r)}")
        break

print(f"1B completed with {errs_1b} errors.")

# Check DP status after 1B
r = xfer([0x05, 0x00, 0x01, 0x06])
print("DP CTRL/STAT after 1B:", list(r))

# Check Set TAR
r = xfer([0x05, 0x00, 0x01, 0x05, 0x00, 0x00, 0x00, 0x24])
print("Set TAR resp:", list(r))

# Check Write block
WORDS_PER_BLOCK = 64
payload = b"\x55" * (WORDS_PER_BLOCK * 4)
req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + payload
resp = xfer(req)
print("Write block resp:", list(resp))

s.close()
