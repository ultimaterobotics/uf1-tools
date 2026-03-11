import argparse, socket, time
from uf1.uf1 import decode_frame, BLK_STATUS, parse_status

TELEM_BLOCK = 0xF1


def decode_umyo_mfg15(mfg: bytes):
    if len(mfg) < 15:
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
    return dataID, batt * 100.0 / 255.0, muscle, (sp0, sp1, sp2, sp3), (qw, qx, qy, qz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=26750)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.5)

    n = 0
    while True:
        try:
            data, src = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            hdr, blocks, _ = decode_frame(data)
        except Exception:
            continue

        st = None
        tele = None
        for t, v in blocks:
            if t == BLK_STATUS:
                st = parse_status(v)
            if t == TELEM_BLOCK:
                tele = v

        if not tele:
            continue
        dec = decode_umyo_mfg15(tele)
        if not dec:
            continue
        dataID, battpct, muscle, sp, quat = dec
        n += 1
        print(
            f"{n} {src[0]} dev=0x{hdr.device_id:08x} len={len(tele)} "
            f"dataID={dataID} batt={battpct:.1f}% muscle={muscle} sp={sp} quat={quat} rssi={st.get('rssi_dbm') if st else None}"
        )


if __name__ == "__main__":
    main()
