import socket
import struct
import sys
import time

def run_diag(host="192.168.4.1", port=3240):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    
    # Connect
    s.sendall(bytes([0x02, 0x01]))
    resp = s.recv(64)
    print("DAP_Connect:", list(resp))
    
    # Try different frequencies
    freqs = [100000, 500000, 1000000, 2000000]
    for freq in freqs:
        f_bytes = struct.pack("<I", freq)
        s.sendall(bytes([0x11]) + f_bytes)
        resp = s.recv(64)
        print(f"\n--- Testing Clock {freq/1000} kHz ---")
        
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
        
        # Try reading DPIDR
        s.sendall(bytes([0x05, 0x00, 0x01, 0x02]))
        resp = s.recv(64)
        print(f"Read DPIDR resp: {list(resp)}")
        if len(resp) >= 7 and resp[1] == 1 and (resp[2] & 0x07) == 1:
            val = struct.unpack("<I", resp[3:7])[0]
            print(f"SUCCESS! DPIDR = 0x{val:08X}")
            break
            
        # Try Dormant-to-SWD sequence (ARM CoreSight ADIv5.2)
        # 8 cycles 1, 128-bit alert, 4 cycles 0, 8-bit activation code 0x1A, 56 cycles 1, 8 cycles 0
        s.sendall(bytes([0x12, 8, 0xFF]))
        s.recv(64)
        alert = bytes([0x92, 0xF3, 0x09, 0x62, 0x95, 0x2D, 0x85, 0x86, 0xE9, 0x3E, 0xA5, 0x44, 0x60, 0x80, 0x00, 0x00])
        s.sendall(bytes([0x12, 128]) + alert)
        s.recv(64)
        s.sendall(bytes([0x12, 4, 0x00]))
        s.recv(64)
        s.sendall(bytes([0x12, 8, 0x1A])) # SWD activation
        s.recv(64)
        s.sendall(bytes([0x12, 56, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        s.recv(64)
        s.sendall(bytes([0x12, 8, 0x00]))
        s.recv(64)
        
        s.sendall(bytes([0x05, 0x00, 0x01, 0x02]))
        resp = s.recv(64)
        print(f"After Dormant-to-SWD Read DPIDR: {list(resp)}")
        if len(resp) >= 7 and resp[1] == 1 and (resp[2] & 0x07) == 1:
            val = struct.unpack("<I", resp[3:7])[0]
            print(f"SUCCESS! DPIDR = 0x{val:08X}")
            break

    s.sendall(bytes([0x03]))
    s.close()

if __name__ == "__main__":
    run_diag()
