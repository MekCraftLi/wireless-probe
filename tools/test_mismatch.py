import socket
import struct

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

def xfer_dap(req):
    s.sendall(bytes(req))
    cmd = req[0]
    if cmd in (0x02, 0x03, 0x04, 0x11, 0x12):
        return recv_exact(2)
    elif cmd == 0x05:
        hdr = recv_exact(3)
        count = hdr[1]
        read_bytes = 0
        idx = 3
        for _ in range(count):
            req_val = req[idx]
            idx += 1
            if req_val & 0x02: read_bytes += 4
            else: idx += 4
        if read_bytes > 0:
            return hdr + recv_exact(read_bytes)
        return hdr
    elif cmd == 0x06:
        hdr = recv_exact(4)
        cnt = hdr[1] | (hdr[2] << 8)
        ack = hdr[3]
        if (req[4] & 0x02): # Read block returns cnt * 4 data bytes regardless of whether it stopped early!
            return hdr + recv_exact(cnt * 4)
        return hdr

xfer_dap([0x02, 0x01])
xfer_dap(bytes([0x11]) + struct.pack("<I", SWD_CLOCK_HZ))
xfer_dap([0x04, 0x01, 0x32, 0x00, 0x00, 0x00]) # idle_cycles = 1

xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 16, 0x9E, 0xE7])
xfer_dap([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
xfer_dap([0x12, 8, 0x00])

xfer_dap([0x05, 0x00, 0x01, 0x02])
xfer_dap([0x05, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x50])
xfer_dap([0x05, 0x00, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00])
xfer_dap([0x05, 0x00, 0x01, 0x01, 0x52, 0x00, 0x00, 0x23])

# Write 256 blocks of 64 words (65536 bytes = 64 KB)
WORDS_PER_BLOCK = 64
BYTES_PER_BLOCK = WORDS_PER_BLOCK * 4
NUM_BLOCKS = 256
TOTAL_BYTES = NUM_BLOCKS * BYTES_PER_BLOCK
DEST_ADDR = 0x24000000
payload = bytearray([(i * 5 + 3) & 0xFF for i in range(TOTAL_BYTES)])

for b_idx in range(NUM_BLOCKS):
    cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
    r_tar = xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    chunk = payload[b_idx * BYTES_PER_BLOCK : (b_idx + 1) * BYTES_PER_BLOCK]
    req = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0D]) + chunk
    resp_w = xfer_dap(req)
    print(f"Write block {b_idx} (addr=0x{cur_addr:08X}): TAR_resp={list(r_tar)}, Write_resp={list(resp_w)}")

readback = bytearray()
for b_idx in range(NUM_BLOCKS):
    cur_addr = DEST_ADDR + b_idx * BYTES_PER_BLOCK
    r_tar = xfer_dap([0x05, 0x00, 0x01, 0x05] + list(struct.pack("<I", cur_addr)))
    req_r = bytes([0x06, 0x00, WORDS_PER_BLOCK & 0xFF, (WORDS_PER_BLOCK >> 8) & 0xFF, 0x0F])
    resp_r = xfer_dap(req_r)
    print(f"Read block {b_idx} (addr=0x{cur_addr:08X}): TAR_resp={list(r_tar)}, Read_len={len(resp_r)}, Read_hdr={list(resp_r[:4])}")
    readback += resp_r[4:]

match = (payload == readback)
print(f"1024 bytes match: {match}")
if not match:
    for i in range(TOTAL_BYTES):
        if payload[i] != readback[i]:
            print(f"Mismatch at byte {i} (block {i//BYTES_PER_BLOCK}, offset {i%BYTES_PER_BLOCK}): expected 0x{payload[i]:02X}, got 0x{readback[i]:02X}")
            break
    print("Block 2 (512:544) readback:", [hex(b) for b in readback[512:544]])
    print("Block 2 (512:544) expected:", [hex(b) for b in payload[512:544]])
    print("Block 3 (768:800) readback:", [hex(b) for b in readback[768:800]])
    print("Block 3 (768:800) expected:", [hex(b) for b in payload[768:800]])

s.close()
