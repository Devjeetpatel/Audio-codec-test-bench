# 🎵 Bluetooth Codec Test Bench

> **A Linux tool that turns your laptop into a Bluetooth headphone to test which audio codecs your Android phone actually supports.**

---

## ⚠️ Important Disclaimer

This program was tested on a **single device: Motorola Edge 50 Neo**.

| Codec | Status on Moto Edge 50 Neo |
|-------|---------------------------|
| SBC | ✅ Confirmed working |
| AAC | ✅ Confirmed working |
| LDAC | ✅ Confirmed working |
| LHDC V2 / V3 / V4 / V5 | ⚠️ Shows in Developer Options but stream does not open |
| aptX / aptX-HD / aptX-Adaptive / aptX TWS+ | ❓ Not tested (phone uses MediaTek chipset, expected to fall back to SBC) |

**We cannot guarantee this tool works correctly on any other phone.** If you test it on your device, please open an issue or pull request and let us know what worked and what didn't. Every report helps improve the tool.

---

## 🤖 About This Project

**This program was built with AI assistance** — specifically using [Claude](https://claude.ai) by Anthropic. The codec byte structures, AVDTP protocol logic, HCI reset sequences, bumble integration, and GUI were all developed through a collaborative back-and-forth conversation with Claude over multiple sessions. The human contributor provided the testing device, described real-world bugs from live runs, and directed what to fix and improve.

We believe in being transparent about AI involvement in software. This doesn't make the code less valid — it just means AI was the primary coding tool used to build it.

---

## 📋 What It Does

Android phones support multiple Bluetooth audio codecs (SBC, AAC, LDAC, LHDC, aptX, etc.). There's no simple way to verify *which* ones your specific phone actually negotiates and opens a stream with — Developer Options can show codecs as available even if the phone silently falls back to AAC when you select them.

This tool solves that by making your Linux laptop pretend to be a Bluetooth headphone. When your phone connects and opens an audio stream, the terminal (or GUI) reports exactly which codec was negotiated and opened. No guessing.

**What you can test:**
- SBC, AAC
- LDAC (Sony)
- LHDC V2, V3, V4, V5 (Savitech / HiBy)
- aptX, aptX-HD, aptX-Adaptive, aptX TWS+ (Qualcomm)

---

## 🛠️ Technologies Used

| Technology | Purpose |
|---|---|
| **Python 3.10+** | Main language |
| **[bumble](https://github.com/google/bumble)** | Pure-Python Bluetooth stack — handles HCI, AVDTP, A2DP protocol |
| **tkinter** | Built-in Python GUI toolkit — no extra GUI dependencies |
| **asyncio** | Asynchronous event loop for bumble |
| **hciconfig / rfkill** | Linux HCI interface management (system tools, pre-installed) |
| **pacat / ffplay** | Optional: audio output for SBC streams (PipeWire or ffmpeg) |

**Why bumble?** It's a pure-Python Bluetooth stack from Google that gives direct access to HCI, AVDTP, and A2DP layers without going through BlueZ. This lets us register custom codec endpoints that BlueZ would normally reject.

---

## 📂 Files

```
bt-codec-bench/
├── codec_tester_gui.py    # GUI application (main file)
├── codec_tester.py        # Terminal / CLI version
├── setup.sh               # One-time setup script
└── README.md              # This file
```

---

## 🚀 Setup (One Time)

### Requirements
- Ubuntu 20.04+ or any Debian-based Linux distro
- A Bluetooth adapter (built-in or USB dongle)
- Python 3.10 or newer
- Root/sudo access

### Step 1 — Run the setup script

```bash
chmod +x setup.sh
./setup.sh
```

This script will:
1. Install required system packages (`python3-venv`, `python3-tk`, `rfkill`)
2. Create a Python virtual environment at `~/bumble/`
3. Install the `bumble` library inside it
4. Disable the system Bluetooth daemon (it conflicts with bumble's direct HCI access)
5. Optionally set up passwordless sudo for `hciconfig` and `rfkill`

### Step 2 — Allow display access for root (GUI only)

The GUI must run as root to access the HCI socket. Run this once per session in your normal user terminal:

```bash
xhost +si:localuser:root
```

---

## ▶️ Running the Tool

### GUI version (recommended)

```bash
source ~/bumble/bin/activate
sudo rfkill unblock all
sudo hciconfig hci0 down
sudo $(which python3) codec_tester_gui.py
```

### Terminal / CLI version

```bash
source ~/bumble/bin/activate
sudo rfkill unblock all
sudo hciconfig hci0 down
sudo $(which python3) codec_tester.py
```

You can also pass a codec directly to skip the menu:

```bash
sudo $(which python3) codec_tester.py hci-socket:0 LDAC
sudo $(which python3) codec_tester.py hci-socket:0 LHDC_V3
```

---

## 📱 How to Test a Codec on Your Phone

Once the tool is running and showing **"Discoverable — waiting for phone..."**:

1. On your phone, go to **Settings → Bluetooth** and pair with **"Codec Test Bench"**
2. Enable **HD audio** if there's a toggle for it
3. Go to **Developer Options → Bluetooth Audio Codec**
4. Select the codec you want to test
5. Disconnect and reconnect the device
6. Watch the terminal or GUI log for:

```
✅  STREAM OPENED  →  LDAC
```

If you see your chosen codec name — it's supported and working. If you see `AAC` or `SBC` instead, the phone fell back because it doesn't support that codec (or negotiation failed).

---

## 🖥️ GUI Features

- **Codec checkboxes** grouped by family (Standard / aptX / LDAC / LHDC)
- **Preset buttons**: All LHDC, All aptX, All codecs, Clear
- **Status cards** showing: Connection state, Active codec name, Sample rate, Bit depth, Bitrate
- **Live bitrate bar chart** — updates every second from actual bytes received
- **Color-coded log panel** — green for streams, yellow for warnings, red for errors
- **Optional SBC audio output** — checkbox to hear audio through your laptop speakers via PipeWire/ffplay

---

## 🔧 Troubleshooting

**`Device or resource busy` when starting:**
```bash
sudo rfkill unblock all
sudo hciconfig hci0 down
```
Then try again.

**GUI window doesn't open when running with sudo:**
```bash
xhost +si:localuser:root
```
Run this in your regular user terminal before launching with sudo.

**`No module named 'bumble'`:**
Make sure you activated the venv first:
```bash
source ~/bumble/bin/activate
```
Then use `sudo $(which python3)` — this picks up the venv Python.

**Phone connects but always shows SBC:**
- Make sure HD audio toggle is ON in your phone's Bluetooth settings
- Unpair and re-pair completely (not just disconnect)
- Try the codec in single-test mode rather than "All codecs"
- Some MediaTek phones only support SBC + AAC natively

**LHDC shows as available but stream opens as AAC:**
This is the known open issue. LHDC negotiation via bumble is partially implemented. The stream registers and the codec appears in Developer Options, but Android's AVDTP SET_CONFIGURATION response handling is still inconsistent. Contributions welcome.

---

## 🤝 Contributing

Found a bug? Tested on a new phone? Got LHDC working? Please contribute!

- **Open an issue** to report your phone model and which codecs worked
- **Open a pull request** for code improvements
- **Star the repo** if this helped you

Especially useful: if you find a way to make LHDC streams open reliably, please share — that's the main open problem.

---

## 📄 License

This project is licensed under the **MIT License**.

```
MIT License

Copyright (c) 2026 Devjeetpatel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 🙏 Acknowledgements

- [Google bumble](https://github.com/google/bumble) — the Python Bluetooth stack that makes all of this possible
- [Anthropic Claude](https://claude.ai) — AI assistant used to build and debug the majority of this codebase
- Motorola Edge 50 Neo — the test device that exposed every bug

---

*Built on Ubuntu 24.04 · Tested February 2026*
