import asyncio
from bleak import BleakScanner

async def main():
    devices = await BleakScanner.discover(timeout=2.0)

    flag = False

    for device in devices:
        name = device.name or ""

        if name.startswith("OpenEarable"):
            flag = True
            print("FOUND DEVICE:")
            print(device)
            print(f"Name: {device.name}, Address: {device.address}")

    if not flag:
        print("No OpenEarable device found.")

asyncio.run(main())