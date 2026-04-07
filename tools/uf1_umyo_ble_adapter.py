#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import socket
import struct
import sys
import zlib
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from uf1.uf1 import (
    BLK_DEVICE_NAME,
    BLK_EMG_RAW,
    BLK_IMU_6DOF,
    BLK_MAG_3,
    BLK_QUAT,
    BLK_STATUS,
    TIME_SRC_PRESENT,
    TIME_US_IS_RX,
    UF1Header,
    build_emg_raw,
    build_status,
    encode_frame,
)

UMYO_SERVICE_UUID = "93375900-F229-8B49-B397-44B5899B8601".lower()
UMYO_TELEMETRY_UUID = "FC7A850D-C1A5-F61F-0DA7-9995621FBD01".lower()
UMYO_NAME_UUID = "FC7A850D-C1A5-F61F-0DA7-9995621FBD02".lower()
SAMPLE_RATE_HZ = 1150


def advertised_name(device: BLEDevice, adv: AdvertisementData | None) -> str:
    if adv and adv.local_name:
        return adv.local_name
    return device.name or ""


def derive_device_id(address: str) -> int:
    return zlib.crc32(address.encode("utf-8")) & 0xFFFFFFFF


def status_block(
    t_src_sample: int,
    rssi_dbm: int,
    mode: int,
    *,
    include_tsrc: bool,
    sample_rate_hz: int,
) -> tuple[bytes, int]:
    flags = TIME_US_IS_RX | (TIME_SRC_PRESENT if include_tsrc else 0)
    return (
        build_status(
            t_src_sample=t_src_sample if include_tsrc else 0,
            sample_rate_hz=sample_rate_hz,
            battery_pct=255,
            rssi_dbm=rssi_dbm,
            mode=mode,
            status_flags=0,
        ),
        flags,
    )


def parse_v2(payload: bytes) -> list[tuple[int, int, bool, int, list[tuple[int, bytes]]]]:
    fmt = payload[0]
    if fmt == 0x01 and len(payload) == 61:
        t_src = struct.unpack_from("<I", payload, 1)[0]
        samples = list(struct.unpack_from("<24h", payload, 5))
        quat = payload[53:61]
        return [
            (t_src, 0, True, SAMPLE_RATE_HZ, [(BLK_EMG_RAW, build_emg_raw(samples))]),
            (t_src, 0, True, 0, [(BLK_QUAT, quat)]),
        ]
    if fmt == 0x02 and len(payload) == 53:
        t_src = struct.unpack_from("<I", payload, 1)[0]
        samples = list(struct.unpack_from("<24h", payload, 5))
        return [(t_src, 1, True, SAMPLE_RATE_HZ, [(BLK_EMG_RAW, build_emg_raw(samples))])]
    if fmt == 0x03 and len(payload) == 27:
        return [(
            0,
            1,
            False,
            0,
            [
                (BLK_IMU_6DOF, payload[1:13]),
                (BLK_MAG_3, payload[13:19]),
                (BLK_QUAT, payload[19:27]),
            ],
        )]
    return []


def parse_legacy(payload: bytes) -> list[tuple[int, int, bool, int, list[tuple[int, bytes]]]]:
    size = len(payload)
    if size in (20, 36, 52):
        t_src = struct.unpack_from("<I", payload, 0)[0]
        sample_count = (size - 4) // 2
        samples = list(struct.unpack_from(f"<{sample_count}h", payload, 4))
        return [(t_src, 0, True, SAMPLE_RATE_HZ, [(BLK_EMG_RAW, build_emg_raw(samples))])]
    if size == 60:
        t_src = struct.unpack_from("<I", payload, 0)[0]
        samples = list(struct.unpack_from("<24h", payload, 4))
        quat = payload[52:60]
        return [
            (t_src, 0, True, SAMPLE_RATE_HZ, [(BLK_EMG_RAW, build_emg_raw(samples))]),
            (t_src, 0, True, 0, [(BLK_QUAT, quat)]),
        ]
    if size == 26:
        return [(
            0,
            0,
            False,
            0,
            [
                (BLK_IMU_6DOF, payload[0:12]),
                (BLK_MAG_3, payload[12:18]),
                (BLK_QUAT, payload[18:26]),
            ],
        )]
    return []


class DeviceBridge:
    def __init__(
        self,
        device: BLEDevice,
        adv: AdvertisementData | None,
        sock: socket.socket,
        udp_addr: tuple[str, int],
    ):
        self.device = device
        self.sock = sock
        self.udp_addr = udp_addr
        self.rssi_dbm = getattr(adv, "rssi", None) or -128
        self.name = advertised_name(device, adv) or device.address
        self.device_id = derive_device_id(device.address)
        self.seq = 0

    def send_frame(self, blocks: list[tuple[int, bytes]], flags: int) -> None:
        frame = encode_frame(
            device_id=self.device_id,
            seq=self.seq,
            t_us=UF1Header.now_rx_time_us(),
            blocks=blocks,
            flags=flags,
        )
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        self.sock.sendto(frame, self.udp_addr)

    def send_name(self) -> None:
        status, flags = status_block(
            0,
            self.rssi_dbm,
            0,
            include_tsrc=False,
            sample_rate_hz=0,
        )
        self.send_frame(
            [(BLK_STATUS, status), (BLK_DEVICE_NAME, self.name.encode("utf-8")[:32])],
            flags,
        )

    def handle_payload(self, payload: bytes) -> None:
        packets = parse_v2(payload) if len(payload) in (27, 53, 61) and payload[:1] in (b"\x01", b"\x02", b"\x03") else parse_legacy(payload)
        if not packets:
            return

        for t_src, mode, include_tsrc, sample_rate, blocks in packets:
            status, flags = status_block(
                t_src,
                self.rssi_dbm,
                mode,
                include_tsrc=include_tsrc,
                sample_rate_hz=sample_rate,
            )
            self.send_frame([(BLK_STATUS, status), *blocks], flags)

    async def run(self, stop_event: asyncio.Event) -> None:
        try:
            async with BleakClient(self.device) as client:
                await self.read_name(client)
                self.send_name()
                await client.start_notify(UMYO_TELEMETRY_UUID, self.notification_handler)
                next_name = asyncio.get_running_loop().time() + 5.0
                try:
                    while not stop_event.is_set():
                        now = asyncio.get_running_loop().time()
                        if now >= next_name:
                            self.send_name()
                            next_name = now + 5.0
                        await asyncio.sleep(0.2)
                finally:
                    with contextlib.suppress(Exception):
                        await client.stop_notify(UMYO_TELEMETRY_UUID)
        except Exception as exc:
            print(f"[ble] {self.device.address}: {exc}", file=sys.stderr)

    async def read_name(self, client: BleakClient) -> None:
        with contextlib.suppress(Exception):
            raw = await client.read_gatt_char(UMYO_NAME_UUID)
            name = bytes(raw).decode("utf-8", errors="ignore").strip("\x00 \t\r\n")
            if name:
                self.name = name

    def notification_handler(self, _: Any, data: bytearray) -> None:
        self.handle_payload(bytes(data))


async def scan_devices(timeout: float) -> list[tuple[BLEDevice, AdvertisementData | None]]:
    raw = await BleakScanner.discover(timeout=timeout, return_adv=True)
    items = raw.values() if isinstance(raw, dict) else raw
    devices: list[tuple[BLEDevice, AdvertisementData | None]] = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 2:
            device, adv = item
        else:
            device, adv = item, None
        devices.append((device, adv))
    devices.sort(key=lambda item: (advertised_name(item[0], item[1]), item[0].address))
    return devices


def matching_devices(
    devices: list[tuple[BLEDevice, AdvertisementData | None]],
    args: argparse.Namespace,
) -> list[tuple[BLEDevice, AdvertisementData | None]]:
    matches: list[tuple[BLEDevice, AdvertisementData | None]] = []
    wanted_address = args.address.lower() if args.address else None

    for device, adv in devices:
        address = device.address.lower()
        name = advertised_name(device, adv)
        service_uuids = {uuid.lower() for uuid in (adv.service_uuids or [])} if adv else set()

        if wanted_address:
            if address == wanted_address:
                matches.append((device, adv))
            continue
        if args.name:
            if args.name.lower() in name.lower():
                matches.append((device, adv))
            continue
        if name.startswith(args.name_prefix) or UMYO_SERVICE_UUID in service_uuids:
            matches.append((device, adv))

    return matches


def print_devices(devices: list[tuple[BLEDevice, AdvertisementData | None]]) -> None:
    for device, adv in devices:
        name = advertised_name(device, adv) or "(no name)"
        rssi = getattr(adv, "rssi", None)
        services = list(adv.service_uuids or []) if adv else []
        print(f"{name}\t{device.address}\trssi={rssi}\tservices={services or '-'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge direct uMyo BLE GATT telemetry to UF1 UDP.")
    parser.add_argument("--list", action="store_true", help="Scan and print nearby BLE devices.")
    parser.add_argument("--scan-timeout", type=float, default=8.0, help="Seconds to scan before selecting devices.")
    parser.add_argument("--name-prefix", default="uMyo-", help="Advertised-name prefix used for automatic matching.")
    parser.add_argument("--name", help="Match devices whose advertised name contains this substring.")
    parser.add_argument("--address", help="Connect to a specific BLE address / CoreBluetooth UUID.")
    parser.add_argument("--all", action="store_true", help="Connect to all matching devices.")
    parser.add_argument("--host", default="127.0.0.1", help="UF1 UDP destination host.")
    parser.add_argument("--port", type=int, default=26750, help="UF1 UDP destination port.")
    parser.add_argument("--run-seconds", type=float, default=0.0, help="Stop automatically after this many seconds.")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    devices = await scan_devices(args.scan_timeout)
    if args.list:
        print_devices(devices)
        return

    matches = matching_devices(devices, args)
    if not matches:
        raise SystemExit("No matching uMyo devices found. Re-run with --list or specify --address.")
    if not args.all and not args.address and len(matches) > 1:
        print_devices(matches)
        raise SystemExit("Multiple devices matched. Re-run with --all or --address.")

    selected = matches if (args.all or args.address) else matches[:1]
    udp_addr = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop)

    bridges = [DeviceBridge(device, adv, sock, udp_addr) for device, adv in selected]
    for bridge in bridges:
        print(f"[ble] connecting {bridge.name} [{bridge.device.address}] -> {args.host}:{args.port}")

    tasks = [asyncio.create_task(bridge.run(stop_event)) for bridge in bridges]

    def maybe_stop(_: asyncio.Task[Any]) -> None:
        if all(task.done() for task in tasks):
            stop_event.set()

    for task in tasks:
        task.add_done_callback(maybe_stop)

    try:
        if args.run_seconds > 0:
            await asyncio.sleep(args.run_seconds)
            stop_event.set()
        else:
            await stop_event.wait()
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        sock.close()


def main() -> None:
    try:
        asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
