"""
Bluetooth Codec Test Bench v5
Turns your Linux laptop into a Bluetooth headphone to test which
audio codecs your phone supports.

Usage:
    sudo rfkill unblock all
    sudo hciconfig hci0 down
    sudo $(which python3) codec_tester.py
"""

import sys
import asyncio
import subprocess
import time
import readline

from bumble.device import Device
from bumble.transport import open_transport
from bumble.avdtp import Listener, MediaCodecCapabilities, MediaType
from bumble.a2dp import make_audio_sink_service_sdp_records

DEFAULT_TRANSPORT = 'hci-socket:0'
HCI_IFACE = 'hci0'


# Patch bumble to accept any vendor codec SET_CONFIGURATION that matches
# vendor_id + codec_id, without rejecting on parameter byte differences.
# Without this, LHDC/aptX streams are rejected and Android falls back to AAC.
def _patch_bumble():
    try:
        from bumble.a2dp import VendorMediaCodecInformation

        def _check(self, config):
            if not hasattr(config, 'vendor_id'):
                return
            if config.vendor_id != self.vendor_id:
                raise ValueError(f"vendor_id mismatch")
            if config.codec_id != self.codec_id:
                raise ValueError(f"codec_id mismatch")

        VendorMediaCodecInformation.check_configuration = _check
        return True
    except Exception as e:
        print(f"  [!] Bumble patch failed: {e}")
        return False

PATCH_OK = _patch_bumble()

# Vendor IDs (little-endian, 4 bytes)
QUALCOMM  = b'\xD0\x00\x00\x00'
SONY      = b'\x2D\x01\x00\x00'
SAVITECH  = b'\x3A\x05\x00\x00'

# Codec catalogue: { KEY: (display_name, media_codec_type, codec_info_bytes) }
# media_codec_type: 0x00=SBC, 0x02=AAC, 0xFF=Vendor
CODECS = {
    "SBC": (
        "SBC", 0x00,
        b'\xFF\xFF\x02\x35'
    ),
    "AAC": (
        "AAC", 0x02,
        b'\xF0\x01\x04\x00\xFF\xFF'
    ),
    "APTX": (
        "aptX", 0xFF,
        QUALCOMM + b'\x01\x00' + b'\xFF'
    ),
    "APTX_HD": (
        "aptX-HD", 0xFF,
        QUALCOMM + b'\x24\x00' + b'\xFF\x00\x00\x00\x00'
    ),
    "APTX_ADAPTIVE": (
        "aptX-Adaptive", 0xFF,
        QUALCOMM + b'\xAD\x00' + b'\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00'
    ),
    "APTX_TWS_PLUS": (
        "aptX TWS+", 0xFF,
        QUALCOMM + b'\x05\x00' + b'\xFF'
    ),
    "LDAC": (
        "LDAC", 0xFF,
        SONY + b'\xAA\x00' + b'\x3C\x07'
    ),
    # LHDC capability byte[0]: bits[7:4]=version, bits[3:0]=sample rate flags
    # Codec IDs: V2=0x4C32, V3=0x4C33, V4=0x4C34, V5=0x4C35
    "LHDC_V2": (
        "LHDC V2", 0xFF,
        SAVITECH + b'\x32\x4C' + b'\x26\xF0\x00'
    ),
    "LHDC_V3": (
        "LHDC V3", 0xFF,
        SAVITECH + b'\x33\x4C' + b'\x3E\xF0\x00'
    ),
    "LHDC_V4": (
        "LHDC V4", 0xFF,
        SAVITECH + b'\x34\x4C' + b'\x4E\xF0\x00'
    ),
    "LHDC_V5": (
        "LHDC V5", 0xFF,
        SAVITECH + b'\x35\x4C' + b'\x5F\xF0\x00'
    ),
}

# SBC and AAC are always included — SBC is mandatory for A2DP,
# AAC keeps the HD audio toggle enabled on the phone.
MANDATORY = ["SBC", "AAC"]

PRESETS = {
    "ALL_LHDC": ["LHDC_V2", "LHDC_V3", "LHDC_V4", "LHDC_V5"],
    "ALL_APTX": ["APTX", "APTX_HD", "APTX_ADAPTIVE", "APTX_TWS_PLUS"],
    "STANDARD": ["SBC", "AAC"],
    "ALL":      list(CODECS.keys()),
}

MENU_MAP = {
    "1":  ["SBC"],            "2":  ["AAC"],
    "3":  ["APTX"],           "4":  ["APTX_HD"],
    "5":  ["APTX_ADAPTIVE"],  "6":  ["APTX_TWS_PLUS"],
    "7":  ["LDAC"],
    "8":  ["LHDC_V2"],        "9":  ["LHDC_V3"],
    "10": ["LHDC_V4"],        "11": ["LHDC_V5"],
    "12": PRESETS["ALL_LHDC"],
    "13": PRESETS["ALL_APTX"],
    "14": PRESETS["STANDARD"],
    "15": PRESETS["ALL"],
}

MENU = """
╔══════════════════════════════════════════════════════════════╗
║          BLUETOOTH CODEC TEST BENCH v5                       ║
║          SBC + AAC always included as A2DP base              ║
╠══════════════════════════════════════════════════════════════╣
║   1  │ SBC                                                   ║
║   2  │ AAC                                                   ║
║   3  │ aptX          (Qualcomm — MediaTek phones: SBC only)  ║
║   4  │ aptX-HD       (Qualcomm — MediaTek phones: SBC only)  ║
║   5  │ aptX-Adaptive (Qualcomm — MediaTek phones: SBC only)  ║
║   6  │ aptX TWS+     (Qualcomm — MediaTek phones: SBC only)  ║
║   7  │ LDAC                                                  ║
║   8  │ LHDC V2                                               ║
║   9  │ LHDC V3       (Android shows as "LHDC V3/V4")         ║
║  10  │ LHDC V4                                               ║
║  11  │ LHDC V5                                               ║
╠══════════════════════════════════════════════════════════════╣
║  12  │ All LHDC  (V2 + V3 + V4 + V5)                         ║
║  13  │ All aptX  (aptX + HD + Adaptive + TWS+)               ║
║  14  │ Standard  (SBC + AAC only)                            ║
║  15  │ ALL codecs                                            ║
╠══════════════════════════════════════════════════════════════╣
║   0  │ Exit                                                  ║
╚══════════════════════════════════════════════════════════════╝"""


def reset_hci(iface=HCI_IFACE):
    print(f"\n  [~] Releasing {iface}...")
    subprocess.run(['sudo', 'hciconfig', iface, 'down'], capture_output=True, timeout=5)
    time.sleep(1.0)
    subprocess.run(['sudo', 'rfkill', 'block',   'bluetooth'], capture_output=True, timeout=5)
    time.sleep(0.6)
    subprocess.run(['sudo', 'rfkill', 'unblock', 'bluetooth'], capture_output=True, timeout=5)
    time.sleep(1.8)
    subprocess.run(['sudo', 'hciconfig', iface, 'down'], capture_output=True, timeout=5)
    time.sleep(0.8)
    print(f"  [✓] {iface} ready.\n")


def resolve(keys):
    result = list(keys)
    for m in MANDATORY:
        if m not in result:
            result.append(m)
    return result


def pick_codecs():
    print(MENU)
    while True:
        try:
            raw = input("\n  Enter number (or codec key, 0 to exit): ").strip()
        except EOFError:
            return None

        if not raw:
            continue
        if '\x1b' in raw or raw.startswith('^['):
            print("  [!] Use number keys only.")
            continue
        if raw.lower() in ('0', 'q', 'exit', 'quit'):
            return None
        if raw in MENU_MAP:
            keys = resolve(MENU_MAP[raw])
            print(f"\n  → Registering: {', '.join(CODECS[k][0] for k in keys)}")
            return keys
        upper = raw.upper().replace('-', '_')
        if upper in CODECS:
            keys = resolve([upper])
            print(f"\n  → Registering: {', '.join(CODECS[k][0] for k in keys)}")
            return keys
        print("  [!] Invalid — enter a number from the menu.")


def make_handler(keys):
    state = {'fired': False}

    def on_connection(protocol):
        if state['fired']:
            return
        state['fired'] = True

        names = [CODECS[k][0] for k in keys]
        print(f"\n[!] AVDTP connected — registering: {', '.join(names)}")

        seids = []
        for key in keys:
            name, mct, info = CODECS[key]
            try:
                caps = MediaCodecCapabilities(
                    media_type=MediaType.AUDIO,
                    media_codec_type=mct,
                    media_codec_information=info
                )
                ep = protocol.add_sink(caps)
                _n = name
                ep.on('open', lambda n=_n: print(
                    f"\n{'═'*56}\n  ✅  STREAM OPENED  →  {n}\n{'═'*56}\n"
                ))
                seids.append(f"{name}(SEID {ep.seid})")
                print(f"  [+] {name}  ({len(info)} bytes)  SEID {ep.seid}")
            except Exception as e:
                print(f"  [!] {name} skipped — {e}")

        primary = next((CODECS[k][0] for k in keys if k not in MANDATORY), "SBC")
        print(
            f"\n  [✓] Endpoints: {', '.join(seids)}\n"
            f"\n  ┌── ON YOUR PHONE ─────────────────────────────────────\n"
            f"  │  1. Enable HD audio if it's off\n"
            f"  │  2. Forget device + re-pair  (or disconnect + reconnect)\n"
            f"  │  3. Developer Options → Bluetooth Audio Codec\n"
            f"  │     → select  '{primary}'\n"
            f"  │  4. Watch for  ✅ STREAM OPENED  here\n"
            f"  └──────────────────────────────────────────────────────\n"
        )

    return on_connection


async def _run_async(keys, transport):
    async with await open_transport(transport) as hci:
        device = Device.with_hci("Codec Test Bench", None, hci.source, hci.sink)
        device.classic_enabled = True
        device.le_enabled      = False
        device.class_of_device = 0x240404

        handle = 0x00010001
        device.sdp_service_records[handle] = make_audio_sink_service_sdp_records(handle)

        listener = Listener(Listener.create_registrar(device))
        listener.on('connection', make_handler(keys))

        await device.power_on()
        await device.set_discoverable(True)
        await device.set_connectable(True)

        print("  [✓] Discoverable — waiting for phone...\n"
              "  Press Ctrl+C to return to the menu.\n")

        await asyncio.Event().wait()


def run_session(keys, transport):
    names = [CODECS[k][0] for k in keys]
    label = " + ".join(names) if len(names) <= 4 else f"{len(names)} codecs"

    print(f"\n{'═'*58}")
    print(f"  {label}")
    print(f"{'═'*58}")
    print(f"  Transport : {transport}")
    print(f"  Patch     : {'✓ active' if PATCH_OK else '✗ failed'}\n")

    try:
        asyncio.run(_run_async(keys, transport))
    except KeyboardInterrupt:
        pass


def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TRANSPORT

    # Direct CLI: sudo python3 codec_tester.py hci-socket:0 LHDC_V3
    if len(sys.argv) > 2:
        arg = sys.argv[2].upper().replace('-', '_')
        keys = None
        if arg in CODECS:
            keys = resolve([arg])
        elif arg in MENU_MAP:
            keys = resolve(MENU_MAP[arg])
        if keys:
            run_session(keys, transport)
            reset_hci()
            return
        print(f"[!] Unknown codec '{sys.argv[2]}' — falling to menu.")

    while True:
        keys = pick_codecs()
        if keys is None:
            print("\n[!] Exiting. Goodbye!\n")
            break
        run_session(keys, transport)
        print("  [!] Session ended.")
        reset_hci()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Forced exit.")
