import argparse, socket, time, binascii
from uf1.uf1 import decode_frame
import struct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=26750)
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.5)

    start = time.monotonic()
    n = 0
    while args.seconds == 0.0 or (time.monotonic() - start) < args.seconds:
        try:
            data, src = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            hdr, blocks, _ = decode_frame(data)
        except Exception as e:
            print("Decode error:", e)
            continue

        n += 1
        types = [hex(t) for t, _ in blocks]
        print(
            f"{n} from {src[0]} dev={hex(hdr.device_id)} seq={hdr.seq} blocks={types}"
        )

        for t, v in blocks:
            if t == 0xF0 and len(v) >= 3:
                manu = int.from_bytes(v[0:2], "little")
                rssi = int.from_bytes(v[2:3], "little", signed=True)
                payload = v[3:]
                print(
                    f"  BLE_ADV manu=0x{manu:04x} rssi={rssi} payload={binascii.hexlify(payload).decode()}"
                )

            elif t == 0x04 and len(v) == 6:
                le = struct.unpack("<hhh", v)
                be = struct.unpack(">hhh", v)
                print(f"  MAG raw={binascii.hexlify(v).decode()} le={le} be={be}")

    print("done")


if __name__ == "__main__":
    main()
