"""
uf1_workbench_server.py  —  WebSocket bridge for uMyo Workbench
Reads the existing UDP stream (same as uf1_probe.py) and forwards
parsed device state to the browser as JSON over WebSocket.

Usage:
    cd uf1-tools
    source venv/bin/activate
    export PYTHONPATH=src
    python tools/uf1_workbench_server.py          # opens browser automatically
    python tools/uf1_workbench_server.py --no-browser  # headless
    python tools/uf1_workbench_server.py --udp-port 26750 --ws-port 8765
"""

import asyncio
import argparse
import json
import socket
import struct
import time
import webbrowser
import threading
from pathlib import Path

try:
    import websockets
except ImportError:
    raise SystemExit("Missing dependency: pip install websockets")

from uf1.uf1 import (
    decode_frame,
    parse_emg_raw, parse_status, parse_device_name,
    BLK_EMG_RAW, BLK_IMU_6DOF, BLK_MAG_3, BLK_QUAT, BLK_STATUS, BLK_DEVICE_NAME,
)

# ---------------------------------------------------------------------------
# Device state — one entry per device ID
# ---------------------------------------------------------------------------
class DeviceState:
    WINDOW = 1.0  # seconds for fps calculation

    def __init__(self, dev_id: int):
        self.dev_id = dev_id
        self.name = f"0x{dev_id:08X}"
        self.prev_seq: int | None = None
        self.seq_gaps = 0
        self.emg_missing_chunks = 0
        self.frames_total = 0
        self.emg_frames = 0
        self.quat_frames = 0
        self.aux_frames = 0
        self.tsrc_steps: list[float] = []
        self.last_tsrc: int | None = None
        self.batt: int | None = None
        self.mode: str = "?"
        self.sr: float = 0.0
        self.emg_queue: list[int] = []
        self.last_quat = None
        self.last_aux = None
        self._quat_fresh = False   # set True when quat block arrives; cleared after each to_json()
        self._aux_fresh = False    # same for aux/mag
        # rolling fps windows
        self._emg_ts: list[float] = []
        self._quat_ts: list[float] = []
        self._frame_ts: list[float] = []
        self.last_seen = time.monotonic()

    def _trim(self, lst, now):
        cutoff = now - self.WINDOW
        while lst and lst[0] < cutoff:
            lst.pop(0)

    @property
    def emg_fps(self): return len(self._emg_ts)
    @property
    def quat_fps(self): return len(self._quat_ts)
    @property
    def fps_total(self): return len(self._frame_ts)
    @property
    def avg_tsrc_step(self):
        return round(sum(self.tsrc_steps)/len(self.tsrc_steps), 2) if self.tsrc_steps else 0.0

    def to_json(self) -> dict:
        emg = self.emg_queue[:]
        self.emg_queue.clear()
        result = {
            "id": f"0x{self.dev_id:08X}",
            "name": self.name,
            "emg_fps": self.emg_fps,
            "quat_fps": self.quat_fps,
            "fps_total": self.fps_total,
            "seq_gaps": self.seq_gaps,
            "emg_missing_chunks": self.emg_missing_chunks,
            "avg_tsrc_step": self.avg_tsrc_step,
            "batt": self.batt,
            "mode": self.mode,
            "sr": self.sr,
            "emg": emg,
            "quat": self.last_quat,
            "quat_fresh": self._quat_fresh,
            "aux": self.last_aux,
            "aux_fresh": self._aux_fresh,
            "last_seen": self.last_seen,
        }
        self._quat_fresh = False
        self._aux_fresh = False
        return result


devices: dict[int, DeviceState] = {}
clients: set = set()
name_cache: dict[int, str] = {}


# ---------------------------------------------------------------------------
# UDP reader — runs in a thread, feeds asyncio queue
# ---------------------------------------------------------------------------
def udp_reader(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
               bind_addr: str, udp_port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_addr, udp_port))
    sock.settimeout(0.5)
    print(f"[udp] listening on {bind_addr}:{udp_port}")
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            asyncio.run_coroutine_threadsafe(queue.put(data), loop)
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[udp] error: {e}")


def parse_packet(data: bytes):
    """Parse a UF1 UDP packet and update device state."""
    try:
        hdr, blocks, _ = decode_frame(data)
    except Exception:
        return


    dev = devices.setdefault(hdr.device_id, DeviceState(hdr.device_id))
    if dev.name == f"0x{hdr.device_id:08X}" and hdr.device_id in name_cache:
        dev.name = name_cache[hdr.device_id]
    now = time.monotonic()
    dev.last_seen = now
    dev.frames_total += 1
    dev._frame_ts.append(now)
    dev._trim(dev._frame_ts, now)

    if dev.prev_seq is not None:
        delta = (hdr.seq - dev.prev_seq) & 0xFFFFFFFF
        if delta > 1:
            dev.seq_gaps += delta - 1
    dev.prev_seq = hdr.seq

    tsrc = None
    for blk_type, val in blocks:
        if blk_type == BLK_STATUS:
            st = parse_status(val)
            tsrc = st.get("t_src_sample")
            sr = st.get("sample_rate_hz", 0)
            if sr:
                dev.sr = float(sr)
            mode = st.get("mode", 0)
            dev.mode = "PhoneOpt" if mode == 0 else "FullStream" if mode == 1 else str(mode)
            batt = st.get("battery_pct", 255)
            if batt != 255:
                dev.batt = batt

        elif blk_type == BLK_EMG_RAW:
            dev.emg_frames += 1
            dev._emg_ts.append(now)
            dev._trim(dev._emg_ts, now)
            result = parse_emg_raw(val)
            dev.emg_queue.extend(result.get("samples_i16", []))
            if tsrc is not None:
                if dev.last_tsrc is not None:
                    step = (tsrc - dev.last_tsrc) & 0xFFFFFFFF
                    if 0 < step < 500:
                        dev.tsrc_steps.append(step)
                        if len(dev.tsrc_steps) > 64:
                            dev.tsrc_steps.pop(0)
                        if step > 8:
                            dev.emg_missing_chunks += max(0, step // 8 - 1)
                dev.last_tsrc = tsrc

        elif blk_type == BLK_QUAT:
            dev.quat_frames += 1
            dev._quat_ts.append(now)
            dev._trim(dev._quat_ts, now)
            if len(val) == 8:
                qw, qx, qy, qz = struct.unpack("<4h", val)
                dev.last_quat = {"qw": qw, "qx": qx, "qy": qy, "qz": qz}
                dev._quat_fresh = True

        elif blk_type == BLK_IMU_6DOF:
            dev.aux_frames += 1
            if len(val) == 12:
                ax, ay, az, gx, gy, gz = struct.unpack("<6h", val)
                dev.last_aux = {**(dev.last_aux or {}),
                                "ax": ax, "ay": ay, "az": az,
                                "gx": gx, "gy": gy, "gz": gz}
                dev._aux_fresh = True

        elif blk_type == BLK_MAG_3:
            if len(val) == 6:
                mx, my, mz = struct.unpack("<3h", val)
                dev.last_aux = {**(dev.last_aux or {}),
                                "mx": mx, "my": my, "mz": mz}
                dev._aux_fresh = True

        elif blk_type == BLK_DEVICE_NAME:
            name = parse_device_name(val)
            if name:
                name_cache[hdr.device_id] = name
                dev.name = name


# ---------------------------------------------------------------------------
# WebSocket server
# ---------------------------------------------------------------------------
async def ws_handler(websocket):
    clients.add(websocket)
    print(f"[ws] client connected ({len(clients)} total)")
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)
        print(f"[ws] client disconnected ({len(clients)} total)")


async def broadcast_loop(interval: float = 0.05, allow: list[str] | None = None):
    """Push device state to all connected clients at ~20 fps."""
    while True:
        await asyncio.sleep(interval)
        if not clients:
            continue
        now = time.monotonic()
        # Drop devices not seen for 5 seconds
        stale = [k for k, v in devices.items() if now - v.last_seen > 5.0]
        for k in stale:
            del devices[k]
        if not devices:
            continue
        visible = [
            d for d in devices.values()
            if allow is None or any(tok in d.name for tok in allow)
        ]
        if not visible:
            continue
        payload = json.dumps({
            "type": "state",
            "devices": [d.to_json() for d in visible],
        })
        dead = set()
        for ws in clients:
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        clients.difference_update(dead)


async def udp_consumer(queue: asyncio.Queue):
    while True:
        data = await queue.get()
        try:
            parse_packet(data)
        except Exception as e:
            print(f"[parse] error: {e}")


async def main(args):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2048)

    # Start UDP reader in background thread
    t = threading.Thread(
        target=udp_reader,
        args=(queue, loop, args.bind, args.udp_port),
        daemon=True,
    )
    t.start()

    # Find workbench HTML relative to this script
    html_path = Path(__file__).parent / "umyo_workbench.html"

    ws_url = f"ws://localhost:{args.ws_port}"
    print(f"[ws]  server on {ws_url}")
    if html_path.exists():
        file_url = f"file://{html_path.resolve()}"
        print(f"[gui] {file_url}")
        if not args.no_browser:
            webbrowser.open(file_url)
    else:
        print("[gui] workbench HTML not found — open umyo_workbench.html manually")

    allow = [t.strip() for t in args.allow.split(",")] if args.allow else None
    if allow:
        print(f"[filter] allowing devices matching: {allow}")

    async with websockets.serve(ws_handler, "localhost", args.ws_port):
        await asyncio.gather(
            udp_consumer(queue),
            broadcast_loop(allow=allow),
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="uMyo Workbench WebSocket bridge")
    ap.add_argument("--udp-port", type=int, default=26750)
    ap.add_argument("--ws-port", type=int, default=8765)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--allow", default=None,
                    metavar="TOKENS",
                    help="comma-separated name substrings to allow (e.g. 'E14A,AB91'); all devices shown if omitted")
    asyncio.run(main(ap.parse_args()))
