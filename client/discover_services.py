import asyncio
from bleak import BleakClient, BleakScanner


async def main():
    print("Scanning for OpenEarable device...")
    devices = await BleakScanner.discover(timeout=5.0)

    address = None
    for device in devices:
        name = device.name or ""
        if name.startswith("OpenEarable"):
            print(f"Found: {device.name}  [{device.address}]")
            address = device.address
            break

    if address is None:
        print("No OpenEarable device found.")
        return

    print(f"\nConnecting to {address} ...\n")
    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}\n")
        print("=" * 60)

        for service in client.services:
            print(f"\nSERVICE: {service.uuid}")
            print(f"  Description: {service.description}")

            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"\n  CHARACTERISTIC: {char.uuid}")
                print(f"    Description : {char.description}")
                print(f"    Properties  : {props}")
                print(f"    Handle      : {char.handle}")

                # Try to read readable characteristics
                if "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char.uuid)
                        print(f"    Value (raw) : {value.hex()}")
                        try:
                            print(f"    Value (str) : {value.decode('utf-8')}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"    Read error  : {e}")

        print("\n" + "=" * 60)


asyncio.run(main())