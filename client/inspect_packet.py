import asyncio, struct
from bleak import BleakClient, BleakScanner

SENSOR_CONFIG_CHAR = "34c2e3be-34aa-11eb-adc1-0242ac120002"
SENSOR_DATA_CHAR   = "34c2e3bc-34aa-11eb-adc1-0242ac120002"
ID_EXG = 9

def handler(sender, data: bytearray):
    if len(data) < 2: return
    sid  = data[0]
    size = data[1]
    payload = data[10:10+size]
    print(f"id={sid} size={size} total_bytes={len(data)} payload_hex={payload.hex()}", end="")
    if len(payload) == 4:
        print(f"  → 1×float: {struct.unpack_from('<f', payload)[0]:.4f} µV")
    elif len(payload) == 8:
        ch0, ch1 = struct.unpack_from('<ff', payload)
        print(f"  → ch0={ch0:.4f} µV  ch1={ch1:.4f} µV")
    else:
        print()

async def main():
    devs = await BleakScanner.discover(timeout=5.0)
    addr = next((d.address for d in devs if (d.name or "").startswith("OpenEarable")), None)
    if not addr: print("Not found"); return
    print(f"Found {addr}")
    async with BleakClient(addr) as c:
        await c.start_notify(SENSOR_DATA_CHAR, handler)
        await c.write_gatt_char(SENSOR_CONFIG_CHAR, bytes([ID_EXG, 0, 0x01]), response=True)
        print("Streaming — press Ctrl+C to stop\n")
        await asyncio.sleep(10)

asyncio.run(main())