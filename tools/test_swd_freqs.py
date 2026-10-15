import socket
import struct
import sys

def test_frequencies():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("192.168.4.1", 3240))
    s.sendall(bytes([0x02, 0x01]))
    s.recv(64)
    
    freqs = [50000, 100000, 250000, 500000, 1000000, 2000000, 5000000, 10000000]
    
    for freq in freqs:
        f_bytes = struct.pack("<I", freq)
        s.sendall(bytes([0x11]) + f_bytes)
        s.recv(64)
        
        # Configure
        s.sendall(bytes([0x04, 0x00, 0x32, 0x00, 0x00, 0x00]))
        s.recv(64)
        
        # Line reset + JTAG to SWD + Line reset
        s.sendall(bytes([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        s.recv(64)
        s.sendall(bytes([0x12, 16, 0x9E, 0xE7]))
        s.recv(64)
        s.sendall(bytes([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        s.recv(64)
        s.sendall(bytes([0x12, 8, 0x00]))
        s.recv(64)
        
        # Read DPIDR
        s.sendall(bytes([0x05, 0x00, 0x01, 0x02]))
        resp = s.recv(64)
        if len(resp) >= 7 and resp[1] == 1 and (resp[2] & 0x07) == 1:
            val = struct.unpack("<I", resp[3:7])[0]
            print(f"Clock {freq/1000:6.1f} kHz: [PASS] DPIDR = 0x{val:08X}")
        else:
            print(f"Clock {freq/1000:6.1f} kHz: [FAIL] Raw: {list(resp)}")

    s.sendall(bytes([0x03]))
    s.close()

if __name__ == "__main__":
    test_frequencies()
