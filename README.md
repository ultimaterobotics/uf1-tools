# uf1-tools

Python + browser workbench and protocol tools for the UF1 transport used by uMyo.

**Docs:** https://make.udevices.io
**Discord:** https://discord.com/invite/dEmCPBzv9G

## What this is

Desktop tools for the uMyo BLE pipeline:

**uMyo → Android app (BLE GATT) → UDP → uf1-tools on PC**

Includes a browser-based workbench with real-time per-device EMG waveform, frequency spectrum, 3D orientation, and ACC/GYRO display. Supports 3+ simultaneous devices.

## Setup

```bash
git clone https://github.com/ultimaterobotics/uf1-tools.git
cd uf1-tools
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
export PYTHONPATH=src         # Windows: set PYTHONPATH=src
```

## Running the workbench

```bash
python tools/uf1_workbench_server.py
# opens browser automatically
# then tap Start GATT Raw in the Android app
```

**Start the workbench before tapping Start Streaming** — the device name frame is sent once at stream start.

## Tools

| Script | Purpose |
|---|---|
| `uf1_workbench_server.py` | WebSocket bridge → browser workbench |
| `umyo_workbench.html` | Browser GUI (opens automatically) |
| `uf1_probe.py` | Per-device stats: fps, seq gaps, IMU rates |
| `uf1_view.py` | Simple EMG-only viewer |

## What works

- Multi-device BLE streaming (3 simultaneous confirmed)
- Real-time EMG waveform, spectrum, 3D orientation, ACC/GYRO sparklines per device
- Persistent device naming across reconnects
- OTA firmware update path (via Android app, not this repo)

## What's not yet done

- Export CSV / recording (UI present, file write not implemented)
- USB base station mode in workbench
- Direct PC BLE (no Android bridge)

## Related

- [umyo-android](https://github.com/ultimaterobotics/umyo-android) — Android BLE bridge
- [uMyo firmware](https://github.com/ultimaterobotics/uMyo) — device firmware
- [uMyo_python_tools](https://github.com/ultimaterobotics/uMyo_python_tools) — older Python tools for USB base station mode
