# UF1 Tools

UF1 is a small unified frame format we’re using for uDevices biosignal tools.

This repo contains the Python-side tools and reference code for receiving, decoding, inspecting, and viewing UF1 streams. Right now the main use case is:

**uMyo → Android app over BLE GATT → UF1 over UDP → Python tools on PC**

So this repo is basically the desktop/protocol side of that pipeline.

## What works right now

Current working path:

- uMyo advertises and connects over BLE
- Android app receives telemetry and raw EMG over GATT
- Android wraps incoming data into UF1 and forwards it over UDP
- Python tools in this repo can receive and inspect that stream
- live raw EMG viewing works

At the moment, the most important tools here are:

- `uf1.py` — UF1 encode/decode helpers
- `uf1_dump_udp.py` — quick UDP/UF1 packet dump
- `uf1_view.py` — simple live raw EMG viewer
- `uf1_probe.py` — cadence / timing / drop inspection
- `uf1_umyo_telem15_decode.py` — decoder for the older 15-byte telemetry path
- `uf1_adv_view.py` / `uf1_umyo_adv_decode.py` — older ADV-oriented debug tools

## Current stage

This is still an active work-in-progress repo, but the core phone-bridge path is already real and working.

Right now we have:

- UF1 spec drafted
- Python tools working
- Android phone bridge working
- raw BLE GATT streaming from uMyo working
- live viewing on PC working

So the repo is past the “just a test” stage, but not yet a polished end-user toolkit.

## Next steps

Planned next steps include:

- cleaning up and organizing the tools a bit more
- recording / replay tools
- more polished desktop receiver / GUI
- additional stream profiles and compatibility modes
- using UF1 as the shared format across phone, desktop, and future receivers

## Notes

This repo is intentionally practical and experimental for now.  
The goal is to make the data path easy to inspect, debug, and build on.

If you are here early: yes, things may still move around a bit.