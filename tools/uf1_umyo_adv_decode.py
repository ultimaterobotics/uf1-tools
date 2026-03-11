import argparse, socket, time, binascii
from uf1.uf1 import decode_frame

ADV_BLOCK = 0xF0


def parse_ad_structures(raw: bytes):
    """Parses BLE advertising 'AD structures' from raw scanRecord bytes."""
    i = 0
    n = len(raw)
    while i < n:
        ln = raw[i]
        i += 1
        if ln == 0:
            break
        if i + ln > n:
            break  # truncated
        ad_type = raw[i]
        ad_data = raw[i + 1 : i + ln]  # ln includes type byte
        i += ln
        yield ad_type, ad_data


def find_name_and_mfg(raw: bytes):
    name = None
    mfg = None
    for t, v in parse_ad_structures(raw):
        if t in (0x08, 0x09):  # short/complete local name
            try:
                name = v.decode("utf-8", errors="replace")
            except Exception:
                name = repr(v)
        elif t == 0xFF:  # manufacturer specific data
            mfg = v
    return name, mfg


def decode_umyo_mfg15(mfg: bytes):
    """
    uMyoBleSdk layout (15 bytes):
    [0] dataID
    [1] batteryLevel (0..255)
    [2] sp0 (1 byte, scaled <<8)
    [3] muscleLevel (0..255)
    [4..5] sp1 (u16 BE)
    [6..7] sp2 (u16 BE)
    [8..9] sp3 (u16 BE)
    [10..11] qw (u16 BE)
    [12] qx (1 byte, scaled <<8)
    [13] qy (1 byte, scaled <<8)
    [14] qz (1 byte, scaled <<8)
    """
    if mfg is None or len(mfg) < 15:
        return None

    dataID = mfg[0]
    batt = mfg[1]
    sp0 = mfg[2] << 8
    muscle = mfg[3]
    sp1 = (mfg[4] << 8) | mfg[5]
    sp2 = (mfg[6] << 8) | mfg[7]
    sp3 = (mfg[8] << 8) | mfg[9]
    qw = (mfg[10] << 8) | mfg[11]
    qx = mfg[12] << 8
    qy = mfg[13] << 8
    qz = mfg[14] << 8

    return {
        "dataID": dataID,
        "battery_pct": batt * 100.0 / 255.0,
        "muscle": muscle,
        "spectrum": (sp0, sp1, sp2, sp3),
        "quat_i16": (qw, qx, qy, qz),
    }


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
        except Exception:
            continue

        adv_val = None
        for t, v in blocks:
            if t == ADV_BLOCK:
                adv_val = v
                break
        if not adv_val or len(adv_val) < 3:
            continue

        manu_id = int.from_bytes(adv_val[0:2], "little")
        rssi = int.from_bytes(adv_val[2:3], "little", signed=True)
        raw = adv_val[3:]

        name, mfg = find_name_and_mfg(raw)
        decoded = decode_umyo_mfg15(mfg)

        n += 1
        if decoded:
            sp0, sp1, sp2, sp3 = decoded["spectrum"]
            qw, qx, qy, qz = decoded["quat_i16"]
            print(
                f"{n} {src[0]} dev={hex(hdr.device_id)} rssi={rssi} name={name} "
                f"dataID={decoded['dataID']} batt={decoded['battery_pct']:.1f}% "
                f"muscle={decoded['muscle']} "
                f"sp=[{sp0},{sp1},{sp2},{sp3}] "
                f"quat=[{qw},{qx},{qy},{qz}]"
            )
        else:
            # still useful debug
            print(
                f"{n} {src[0]} dev={hex(hdr.device_id)} rssi={rssi} name={name} "
                f"manu_id=0x{manu_id:04x} mfg_len={(len(mfg) if mfg else 0)} raw_len={len(raw)}"
            )


if __name__ == "__main__":
    main()
