"""
Step 1: LISTEN ONLY.

Connects to a Bluetooth LE band, subscribes to every characteristic that can
send data, and saves each message to a file. It NEVER writes anything to the
band, so it cannot change or harm it.

Usage (from the whoop_py folder, with the venv active):
    python step1_listen/listen.py                 # scan, pick from a list
    python step1_listen/listen.py --name whoop    # only show devices with "whoop" in the name
    python step1_listen/listen.py --seconds 300   # listen for 5 minutes (default 120)

Output:
    data/raw/raw_<date>_<time>.jsonl   one JSON object per message
    data/raw/raw_<date>_<time>_services.txt   the band's services/characteristics
"""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


async def pick_device(name_filter):
    print("Scanning for 10 seconds...")
    found = await BleakScanner.discover(timeout=10, return_adv=True)

    rows = []
    for address, (device, adv) in found.items():
        name = device.name or adv.local_name or "(no name)"
        if name_filter and name_filter.lower() not in name.lower():
            continue
        rows.append((adv.rssi, address, name, adv.service_uuids))

    if not rows:
        print("No matching devices found.")
        print("Check: official WHOOP app closed, band not connected to your phone,")
        print("PC Bluetooth on, band close by. Try again without --name.")
        return None

    rows.sort(reverse=True)  # strongest signal first
    for i, (rssi, address, name, uuids) in enumerate(rows):
        print(f"[{i}] {name:30} {address}  signal={rssi} dBm")
        if uuids:
            print(f"      advertises: {', '.join(uuids)}")

    choice = input("Type the number of your band: ").strip()
    try:
        return rows[int(choice)][1]
    except (ValueError, IndexError):
        print("Not a valid choice.")
        return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="only list devices whose name contains this")
    parser.add_argument("--seconds", type=int, default=120, help="how long to listen")
    parser.add_argument("--pair", action="store_true", help="do standard BLE pairing after connecting")
    parser.add_argument("--address", help="band address, e.g. E9:B9:91:XX:XX:XX (skips the list)")
    args = parser.parse_args()

    if args.address:
        print(f"Looking for {args.address} (up to 20s)...")
        address = await BleakScanner.find_device_by_address(args.address, timeout=20)
        if address is None:
            print("Band not found. It is probably still connected to your phone or the")
            print("WHOOP app. Force-stop the app, turn phone Bluetooth off, keep the band")
            print("next to the PC, and try again.")
            return
    else:
        address = await pick_device(args.name)
        if not address:
            return

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = RAW_DIR / f"raw_{stamp}.jsonl"
    services_path = RAW_DIR / f"raw_{stamp}_services.txt"

    count = 0
    start = time.time()

    with open(log_path, "w", encoding="utf-8") as log:

        def on_message(characteristic, data: bytearray):
            nonlocal count
            count += 1
            record = {
                "t": round(time.time(), 3),        # PC clock, seconds
                "char": characteristic.uuid,        # which "mailbox"
                "len": len(data),
                "hex": bytes(data).hex(),
            }
            log.write(json.dumps(record) + "\n")
            log.flush()  # save immediately so a crash loses nothing
            print(f"#{count:<5} {characteristic.uuid[:8]}  {len(data):3} bytes  {bytes(data).hex()[:60]}")

        print(f"Connecting to {address} ...")
        async with BleakClient(address) as client:
            print("Connected.")

            if args.pair:
                print("Pairing... accept any Windows pop-up that appears.")
                try:
                    await client.pair()
                    print("Pairing finished.")
                except Exception as e:
                    print(f"Pairing failed: {e}")

            # Write down everything the band offers.

            with open(services_path, "w", encoding="utf-8") as sf:
                for service in client.services:
                    sf.write(f"SERVICE {service.uuid}\n")
                    for ch in service.characteristics:
                        sf.write(f"  CHAR {ch.uuid}  properties={ch.properties}\n")
            print(f"Saved service list to {services_path.name}")

            # Subscribe (listen) to everything that can send data.
            # start_notify only asks the band to send to us; it writes no data.
            subscribed = 0
            for service in client.services:
                for ch in service.characteristics:
                    if "notify" in ch.properties or "indicate" in ch.properties:
                        try:
                            await client.start_notify(ch.uuid, on_message)
                            subscribed += 1
                            print(f"Listening on {ch.uuid}")
                        except Exception as e:
                            print(f"Could not listen on {ch.uuid}: {e}")

            if subscribed == 0:
                print("Nothing to listen to. The band may need pairing first.")
                return

            print(f"\nListening for {args.seconds}s. Press Ctrl+C to stop early.\n")
            try:
                await asyncio.sleep(args.seconds)
            except asyncio.CancelledError:
                pass

    print(f"\nDone. {count} messages in {time.time() - start:.0f}s.")
    print(f"Saved to {log_path}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by you.")