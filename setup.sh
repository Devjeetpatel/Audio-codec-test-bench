#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Bluetooth Codec Test Bench — One-time setup
#  Run once, then launch with: sudo $(which python3) codec_tester_gui.py
# ─────────────────────────────────────────────────────────────────────────────
set -e

VENV_DIR="$HOME/bumble"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Bluetooth Codec Test Bench — Setup                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. System packages ──────────────────────────────────────────────────────
echo "[1/6] Checking system packages..."
MISSING=()
for pkg in python3 python3-venv python3-tk rfkill; do
    dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "      Installing: ${MISSING[*]}"
    sudo apt-get install -y "${MISSING[@]}" -q
else
    echo "      All system packages present."
fi

# ── 2. Python venv ──────────────────────────────────────────────────────────
echo "[2/6] Setting up Python virtual environment at $VENV_DIR..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "      Created."
else
    echo "      Already exists."
fi

# ── 3. bumble ───────────────────────────────────────────────────────────────
echo "[3/6] Installing/updating bumble..."
"$VENV_DIR/bin/pip" install --upgrade bumble --quiet
echo "      bumble installed: $("$VENV_DIR/bin/pip" show bumble | grep Version)"

# ── 4. Optional: ffmpeg for audio output ───────────────────────────────────
echo "[4/6] Checking audio output (optional)..."
if command -v pacat &>/dev/null; then
    echo "      pacat (PipeWire/PulseAudio) found — audio output supported."
elif command -v ffplay &>/dev/null; then
    echo "      ffplay found — audio output supported."
else
    echo "      Neither pacat nor ffplay found. Audio output disabled."
    echo "      To enable, install PipeWire:  sudo apt-get install pipewire"
fi

# ── 5. Disable system Bluetooth daemon ─────────────────────────────────────
echo "[5/6] Disabling system Bluetooth daemon (it conflicts with bumble)..."
if systemctl is-active --quiet bluetooth; then
    sudo systemctl stop bluetooth
    echo "      Stopped."
fi
sudo systemctl disable bluetooth 2>/dev/null && echo "      Disabled at boot." || true

# ── 6. sudo rules (optional — avoids password prompt every run) ─────────────
echo "[6/6] Setting up sudo rules for hciconfig/rfkill (optional)..."
SUDOERS_FILE="/etc/sudoers.d/bt-codec-bench"
if [ ! -f "$SUDOERS_FILE" ]; then
    read -p "      Allow passwordless hciconfig/rfkill for $USER? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        echo "$USER ALL=(ALL) NOPASSWD: /usr/sbin/hciconfig, /usr/sbin/rfkill" \
            | sudo tee "$SUDOERS_FILE" > /dev/null
        sudo chmod 440 "$SUDOERS_FILE"
        echo "      Done — you can now run the tool with:  sudo \$(which python3) codec_tester_gui.py"
    fi
else
    echo "      Already configured."
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  To run:"
echo "    source $VENV_DIR/bin/activate"
echo "    sudo rfkill unblock all"
echo "    sudo hciconfig hci0 down"
echo "    sudo \$(which python3) $SCRIPT_DIR/codec_tester_gui.py"
echo ""
echo "  Tip: If the GUI doesn't open as root, run first:"
echo "    xhost +si:localuser:root"
echo "══════════════════════════════════════════════════════"
