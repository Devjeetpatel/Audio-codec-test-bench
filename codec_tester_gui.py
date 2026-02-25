#!/usr/bin/env python3
"""
Bluetooth Codec Test Bench — GUI
Turns a Linux laptop into a Bluetooth headphone to probe which audio
codecs your Android phone supports, with live stream monitoring.
"""

import sys, os, asyncio, subprocess, time, threading, queue, struct, re, inspect, shutil
import tkinter as tk
from tkinter import ttk, messagebox

# ─────────────────────────────────────────────────────────
#  CODEC CATALOGUE (Universal Hex Configurations)
# ─────────────────────────────────────────────────────────
QUALCOMM  = b'\xD0\x00\x00\x00'
SONY      = b'\x2D\x01\x00\x00'
SAVITECH  = b'\x3A\x05\x00\x00'

CODECS = {
    "SBC":           ("SBC",           0x00, b'\xFF\xFF\x02\x35'),
    "AAC":           ("AAC",           0x02, b'\xF0\x01\x04\x00\xFF\xFF'),
    "APTX":          ("aptX",          0xFF, QUALCOMM + b'\x01\x00\xFF'),
    "APTX_HD":       ("aptX-HD",       0xFF, QUALCOMM + b'\x24\x00\xFF\x00\x00\x00\x00'),
    "APTX_ADAPTIVE": ("aptX-Adaptive", 0xFF, QUALCOMM + b'\xAD\x00' + bytes(10)),
    "APTX_TWS_PLUS": ("aptX TWS+",     0xFF, QUALCOMM + b'\x05\x00\xFF'),
    "LDAC":          ("LDAC",          0xFF, SONY  + b'\xAA\x00\x3C\x07'),
    # Root Fixes for Xiaomi/OnePlus LHDC compatibility
    "LHDC_V2":       ("LHDC V2",       0xFF, SAVITECH + b'\x32\x4C\x26\xF0\x00'),
    "LHDC_V3":       ("LHDC V3",       0xFF, SAVITECH + b'\x48\x4C\x3E\xF0\x00'), # 0x4C48 is standard for V3
    "LHDC_V4":       ("LHDC V4",       0xFF, SAVITECH + b'\x34\x4C\x4E\xF0\x00'),
    "LHDC_V5":       ("LHDC V5",       0xFF, SAVITECH + b'\x35\x4C\x5F\xF0\x00'),
}

MANDATORY = ["SBC", "AAC"]

FAMILIES = [
    ("Standard",    ["SBC", "AAC"]),
    ("aptX family", ["APTX", "APTX_HD", "APTX_ADAPTIVE", "APTX_TWS_PLUS"]),
    ("Sony LDAC",   ["LDAC"]),
    ("LHDC family", ["LHDC_V2", "LHDC_V3", "LHDC_V4", "LHDC_V5"]),
]

# ─────────────────────────────────────────────────────────
#  CODEC INFO PARSER
# ─────────────────────────────────────────────────────────

def parse_codec_info(codec_key: str, info_bytes: bytes) -> dict:
    r = {}
    try:
        b = info_bytes
        if codec_key == "SBC" and len(b) >= 4:
            for mask, hz in ((0x80, 16000), (0x40, 32000), (0x20, 44100), (0x10, 48000)):
                if b[0] & mask:
                    r['sample_rate'] = hz; break
            r['bit_depth'] = 16
            r['max_kbps'] = 320

        elif codec_key == "AAC" and len(b) >= 2:
            sr_bits = ((b[0] & 0x0F) << 8) | b[1]
            for mask, hz in ((0x800, 8000), (0x400, 11025), (0x200, 12000),
                              (0x100, 16000), (0x080, 22050), (0x040, 24000),
                              (0x020, 32000), (0x010, 44100), (0x008, 48000),
                              (0x002, 88200), (0x001, 96000)):
                if sr_bits & mask:
                    r['sample_rate'] = hz; break
            r['bit_depth'] = 16
            r['max_kbps'] = 320

        elif codec_key == "LDAC" and len(b) >= 8:
            for mask, hz in ((0x20, 44100), (0x10, 48000), (0x04, 88200), (0x02, 96000)):
                if b[6] & mask:
                    r['sample_rate'] = hz; break
            r['bit_depth'] = 24
            q = b[7] & 0x07
            r['max_kbps'] = {0: 990, 1: 660, 2: 330}.get(q, 990)

        elif codec_key.startswith("LHDC") and len(b) >= 7:
            cap0 = b[6]
            for mask, hz in ((0x08, 192000), (0x04, 96000), (0x02, 48000), (0x01, 44100)):
                if cap0 & mask:
                    r['sample_rate'] = hz; break
            r['bit_depth'] = 24
            r['max_kbps'] = 900

        elif codec_key.startswith("APTX") and len(b) >= 7:
            sr_byte = b[6]
            for mask, hz in ((0x80, 44100), (0x40, 48000)):
                if sr_byte & mask:
                    r['sample_rate'] = hz; break
            r['bit_depth'] = 24 if "HD" in codec_key else 16
            r['max_kbps'] = 576 if "HD" in codec_key else 352
    except Exception:
        pass
    return r

# ─────────────────────────────────────────────────────────
#  BUMBLE VENDOR CODEC PATCH
# ─────────────────────────────────────────────────────────

def apply_bumble_patch() -> list:
    """
    Directly overwrites Bumble's MediaCodecCapabilities config checker.
    If the codec is a vendor type (0xFF), we automatically return success.
    This stops Bumble from rejecting Android's specific LHDC/aptX requests.
    """
    try:
        from bumble.avdtp import MediaCodecCapabilities
        
        orig_check = MediaCodecCapabilities.check_configuration
        
        def _permissive_check(self, configuration):
            if self.media_codec_type == 0xFF:
                return 
            return orig_check(self, configuration)
            
        MediaCodecCapabilities.check_configuration = _permissive_check
        return ["SUCCESS: Bumble vendor codec validation bypassed."]
    except Exception as e:
        return [f"ERROR: Failed to patch Bumble: {e}"]

# ─────────────────────────────────────────────────────────
#  HCI RESET
# ─────────────────────────────────────────────────────────

def reset_hci(iface='hci0'):
    for cmd in [
        ['sudo', 'hciconfig', iface, 'down'],
        ['sudo', 'rfkill', 'block',   'bluetooth'],
        ['sudo', 'rfkill', 'unblock', 'bluetooth'],
        ['sudo', 'hciconfig', iface, 'down'],
    ]:
        subprocess.run(cmd, capture_output=True, timeout=5)
        time.sleep(0.8 if 'rfkill' in cmd else 1.0)

# ─────────────────────────────────────────────────────────
#  AUDIO PLAYER (Completely rebuilt for zero-latency)
# ─────────────────────────────────────────────────────────

class AudioPlayer:
    def __init__(self, log_fn):
        self._log = log_fn
        self._proc = None
        self.active = False
        self.dump_file = None

        if shutil.which('ffplay'):
            self._backend = 'ffplay'
        else:
            self._backend = None

    def start(self, sample_rate=44100, channels=2, codec_key='SBC'):
        if not self._backend:
            self._log("  ⚠️ No audio backend found. Install ffmpeg (ffplay).", 'warning')
            return
            
        if codec_key != 'SBC':
            self._log(f"  ⚠️ Live playback only works for SBC. AAC/LDAC/LHDC cannot be played directly.", 'warning')
            return
            
        # The ultimate anti-buffering command for pure, instantaneous playback
        cmd = [
            'ffplay', '-nodisp', '-autoexit', 
            '-f', 'sbc', 
            '-ac', str(channels), 
            '-ar', str(sample_rate),
            '-probesize', '32', 
            '-analyzeduration', '0',
            '-fflags', 'nobuffer', 
            '-flags', 'low_delay',
            '-strict', 'experimental',
            '-i', 'pipe:0'
        ]
                
        try:
            # bufsize=0 is ABSOLUTELY CRITICAL. It prevents Python from choking the stream.
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            self.active = True
            
            # Save the raw stream to your home folder as a failsafe
            dump_path = os.path.expanduser('~/bumble_stream_dump.sbc')
            self.dump_file = open(dump_path, 'wb')
            
            self._log(f"  🔊 Audio playing via ffplay (SBC)", 'success')
            self._log(f"  💾 Saving raw backup stream to: {dump_path}", 'info')
        except Exception as e:
            self._log(f"  ⚠️ Audio failed to start: {e}", 'error')

    def write(self, data: bytes):
        if self.active and self._proc and self._proc.poll() is None:
            try:
                # Shove data into the player instantly
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
                # Dump it to the failsafe file
                if self.dump_file:
                    self.dump_file.write(data)
            except Exception as e:
                self._log(f"  ⚠️ Audio pipe error: {e}", 'error')
                self.active = False

    def stop(self):
        self.active = False
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
            
        if self.dump_file:
            try:
                self.dump_file.close()
            except Exception:
                pass
            self.dump_file = None


# ─────────────────────────────────────────────────────────
#  BENCH WORKER
# ─────────────────────────────────────────────────────────

class BenchWorker:

    def __init__(self, ev_q: queue.Queue, log_fn):
        self._q = ev_q
        self._log_fn = log_fn
        self._stop_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bitrate_task: asyncio.Task | None = None
        self.bytes_recv = 0
        self._audio = AudioPlayer(self._emit_log)
        self._audio_enabled = False

    def _emit(self, ev_type, **kw):
        self._q.put({'type': ev_type, **kw})

    def _emit_log(self, text, level='normal'):
        self._emit('LOG', text=text, level=level)

    def start(self, codec_keys: list, transport: str, audio: bool):
        self._audio_enabled = audio
        t = threading.Thread(target=self._thread_main,
                             args=(codec_keys, transport), daemon=True)
        t.start()

    def stop(self):
        if self._loop and not self._loop.is_closed() and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _thread_main(self, codec_keys, transport):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run(codec_keys, transport))
        except Exception as e:
            self._emit_log(f"[ERROR] {e}", 'error')
        finally:
            self._loop.close()
            self._audio.stop()
            self._emit('DONE')

    async def _run(self, codec_keys, transport):
        from bumble.device import Device
        from bumble.transport import open_transport
        from bumble.avdtp import Listener, MediaCodecCapabilities, MediaType
        from bumble.a2dp import make_audio_sink_service_sdp_records

        self._stop_event = asyncio.Event()

        async with await open_transport(transport) as hci:
            dev = Device.with_hci("Codec Test Bench", None, hci.source, hci.sink)
            dev.classic_enabled = True
            dev.le_enabled      = False
            dev.class_of_device = 0x240404

            h = 0x00010001
            dev.sdp_service_records[h] = make_audio_sink_service_sdp_records(h)

            listener = Listener(Listener.create_registrar(dev))
            fired = {'v': False}

            def on_avdtp(protocol):
                if fired['v']:
                    return
                fired['v'] = True

                names = [CODECS[k][0] for k in codec_keys]
                self._emit_log(f"[AVDTP] Connected — registering: {', '.join(names)}")

                for key in codec_keys:
                    name, mct, info = CODECS[key]
                    try:
                        caps = MediaCodecCapabilities(
                            media_type=MediaType.AUDIO,
                            media_codec_type=mct,
                            media_codec_information=info,
                        )
                        ep = protocol.add_sink(caps)
                        self._emit_log(f"  [+] {name}  SEID {ep.seid}")
                        _k, _n, _ep = key, name, ep

                        def _on_open(k=_k, n=_n, endpoint=_ep):
                            cfg_bytes = b''
                            cfg_obj = getattr(endpoint, 'configuration', None)
                            if cfg_obj:
                                cfg_bytes = bytes(
                                    getattr(cfg_obj, 'media_codec_information', b'') or b''
                                )
                            codec_info = parse_codec_info(k, cfg_bytes)
                            self._emit('STREAM_OPENED', codec=n, key=k,
                                       info=codec_info, cfg_hex=cfg_bytes.hex())

                            if self._audio_enabled:
                                sr = codec_info.get('sample_rate', 44100)
                                self._audio.start(sample_rate=sr, channels=2, codec_key=k)
                                
                                def _on_rtp(pkt):
                                    try:
                                        payload = getattr(pkt, 'payload', bytes(pkt))
                                        # ONLY strip headers and write if it's SBC
                                        if k == 'SBC' and len(payload) > 1:
                                            self._audio.write(payload[1:])
                                        self.bytes_recv += len(payload)
                                    except Exception:
                                        pass
                                endpoint.on('rtp_packet', _on_rtp)

                        def _on_close(n=_n):
                            self._emit('STREAM_CLOSED', codec=n)
                            self._audio.stop()

                        ep.on('open',  _on_open)
                        ep.on('close', _on_close)
                    except Exception as exc:
                        self._emit_log(f"  [!] {name} skipped: {exc}", 'warning')

                self._emit('CONNECTED')

            listener.on('connection', on_avdtp)

            await dev.power_on()
            await dev.set_discoverable(True)
            await dev.set_connectable(True)

            self._emit('DISCOVERABLE')

            async def bitrate_loop():
                prev = 0
                while not self._stop_event.is_set():
                    await asyncio.sleep(1.0)
                    delta = self.bytes_recv - prev
                    prev = self.bytes_recv
                    if delta:
                        self._emit('BITRATE', kbps=delta * 8 / 1000)

            self._bitrate_task = asyncio.ensure_future(bitrate_loop())
            await self._stop_event.wait()
            self._bitrate_task.cancel() # Safely cleanup the task on stop


# ─────────────────────────────────────────────────────────
#  GUI — DARK THEME COLOURS
# ─────────────────────────────────────────────────────────
C = {
    'bg':      '#1e1e2e',
    'panel':   '#2a2a3e',
    'card':    '#313244',
    'accent':  '#7c6af7',
    'green':   '#a6e3a1',
    'red':     '#f38ba8',
    'yellow':  '#f9e2af',
    'blue':    '#89b4fa',
    'text':    '#cdd6f4',
    'dim':     '#6c7086',
}

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Bluetooth Codec Test Bench")
        self.geometry("1180x760")
        self.minsize(960, 640)
        self.configure(bg=C['bg'])
        self.resizable(True, True)

        self._ev_q: queue.Queue = queue.Queue()
        self._worker: BenchWorker | None = None
        self._running = False
        self._codec_vars: dict[str, tk.BooleanVar] = {}
        self._audio_var = tk.BooleanVar(value=False)
        self._bitrate_history: list[float] = []
        self._max_kbps = 1000.0

        self._build_ui()
        self.after(100, self._poll)

        logs = apply_bumble_patch()
        for line in logs:
            self._log(line, 'success' if 'SUCCESS' in line else 'warning')

    def _build_ui(self):
        self._build_header()
        body = tk.Frame(self, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=10, pady=(4, 10))
        self._build_sidebar(body)
        self._build_main(body)

    def _build_header(self):
        hdr = tk.Frame(self, bg=C['accent'], height=50)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🎵  Bluetooth Codec Test Bench",
                 bg=C['accent'], fg='white',
                 font=('Helvetica', 13, 'bold')).pack(side='left', padx=16, pady=10)
        self._hdr_status = tk.Label(hdr, text="● Idle",
                                     bg=C['accent'], fg='white',
                                     font=('Helvetica', 10))
        self._hdr_status.pack(side='right', padx=16)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=C['panel'], width=248)
        sb.pack(side='left', fill='y', padx=(0, 8))
        sb.pack_propagate(False)

        self._section_label(sb, "CODECS")
        cf = tk.Frame(sb, bg=C['panel'])
        cf.pack(fill='x', padx=10)

        for family, keys in FAMILIES:
            tk.Label(cf, text=f"  {family}",
                     bg=C['panel'], fg=C['dim'],
                     font=('Helvetica', 8, 'bold'), anchor='w'
                     ).pack(fill='x', pady=(8, 1))
            for key in keys:
                name = CODECS[key][0]
                note = "  ← mandatory" if key in MANDATORY else ""
                var = tk.BooleanVar(value=(key in MANDATORY))
                self._codec_vars[key] = var
                tk.Checkbutton(
                    cf, text=f"  {name}{note}",
                    variable=var,
                    bg=C['panel'], fg=C['text'],
                    selectcolor=C['bg'],
                    activebackground=C['panel'],
                    activeforeground=C['accent'],
                    font=('Helvetica', 10), anchor='w',
                    cursor='hand2',
                ).pack(fill='x')

        self._section_label(sb, "PRESETS", pady_top=12)
        pf = tk.Frame(sb, bg=C['panel'])
        pf.pack(fill='x', padx=10, pady=2)
        for label, keys in [
            ("All LHDC",   ["LHDC_V2","LHDC_V3","LHDC_V4","LHDC_V5"]),
            ("All aptX",   ["APTX","APTX_HD","APTX_ADAPTIVE","APTX_TWS_PLUS"]),
            ("LDAC only",  ["LDAC"]),
            ("All codecs", list(CODECS.keys())),
            ("Clear",      []),
        ]:
            self._flat_btn(pf, label, lambda k=keys: self._preset(k))

        self._section_label(sb, "AUDIO OUTPUT", pady_top=10)
        tk.Checkbutton(sb, text="  Route SBC to laptop speakers",
                       variable=self._audio_var,
                       bg=C['panel'], fg=C['text'],
                       selectcolor=C['bg'],
                       activebackground=C['panel'],
                       font=('Helvetica', 9), anchor='w',
                       cursor='hand2').pack(fill='x', padx=10)

        bf = tk.Frame(sb, bg=C['panel'])
        bf.pack(fill='x', padx=10, pady=12, side='bottom')
        self._start_btn = tk.Button(bf, text="▶  START TEST",
                                     command=self._start,
                                     bg=C['accent'], fg='white',
                                     activebackground='#9b8bf8',
                                     relief='flat', cursor='hand2', pady=9,
                                     font=('Helvetica', 11, 'bold'))
        self._start_btn.pack(fill='x', pady=(0, 4))
        self._stop_btn = tk.Button(bf, text="■  STOP",
                                    command=self._stop,
                                    bg=C['card'], fg=C['dim'],
                                    activebackground=C['red'],
                                    relief='flat', cursor='hand2', pady=7,
                                    font=('Helvetica', 10), state='disabled')
        self._stop_btn.pack(fill='x')

    def _build_main(self, parent):
        right = tk.Frame(parent, bg=C['bg'])
        right.pack(side='right', fill='both', expand=True)

        cards = tk.Frame(right, bg=C['bg'])
        cards.pack(fill='x', pady=(0, 8))

        stat_defs = [
            ("Connection", '_c_conn',   "—"),
            ("Codec",      '_c_codec',  "—"),
            ("Sample Rate",'_c_sr',     "—"),
            ("Bit Depth",  '_c_bd',     "—"),
            ("Bitrate",    '_c_br',     "—"),
        ]
        for i, (label, attr, default) in enumerate(stat_defs):
            card = tk.Frame(cards, bg=C['card'], pady=8)
            card.grid(row=0, column=i, padx=4, sticky='nsew')
            cards.columnconfigure(i, weight=1)
            tk.Label(card, text=label, bg=C['card'], fg=C['dim'],
                     font=('Helvetica', 8)).pack()
            lbl = tk.Label(card, text=default, bg=C['card'], fg=C['text'],
                           font=('Helvetica', 12, 'bold'))
            lbl.pack()
            setattr(self, attr, lbl)

        chart_frame = tk.Frame(right, bg=C['card'])
        chart_frame.pack(fill='x', pady=(0, 8))
        tk.Label(chart_frame, text="  Live Bitrate",
                 bg=C['card'], fg=C['dim'],
                 font=('Helvetica', 8)).pack(anchor='w', padx=8, pady=(4, 0))
        self._chart = tk.Canvas(chart_frame, height=60, bg=C['bg'],
                                highlightthickness=0)
        self._chart.pack(fill='x', padx=8, pady=4)
        self._draw_chart()

        log_frame = tk.Frame(right, bg=C['panel'])
        log_frame.pack(fill='both', expand=True)
        log_hdr = tk.Frame(log_frame, bg=C['panel'])
        log_hdr.pack(fill='x', padx=8, pady=(4, 0))
        tk.Label(log_hdr, text="LOG", bg=C['panel'], fg=C['accent'],
                 font=('Helvetica', 9, 'bold')).pack(side='left')
        self._flat_btn(log_hdr, "Clear", self._clear_log, side='right', small=True)

        self._log_txt = tk.Text(log_frame, bg=C['bg'], fg=C['text'],
                                font=('Courier', 9), wrap='word',
                                state='disabled', relief='flat',
                                padx=8, pady=4)
        sb_log = ttk.Scrollbar(log_frame, command=self._log_txt.yview)
        self._log_txt.configure(yscrollcommand=sb_log.set)
        sb_log.pack(side='right', fill='y')
        self._log_txt.pack(fill='both', expand=True, padx=(8, 0), pady=(0, 8))

        self._log_txt.tag_config('info',    foreground=C['blue'])
        self._log_txt.tag_config('success', foreground=C['green'])
        self._log_txt.tag_config('warning', foreground=C['yellow'])
        self._log_txt.tag_config('error',   foreground=C['red'])
        self._log_txt.tag_config('stream',
                                  foreground=C['green'],
                                  font=('Courier', 10, 'bold'))

    def _section_label(self, parent, text, pady_top=6):
        tk.Label(parent, text=f"  {text}",
                 bg=C['panel'], fg=C['accent'],
                 font=('Helvetica', 8, 'bold'), anchor='w',
                 pady=pady_top).pack(fill='x')

    def _flat_btn(self, parent, text, cmd, side='top', small=False, **pack_kw):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=C['bg'], fg=C['text'],
                      activebackground=C['accent'], activeforeground='white',
                      relief='flat', cursor='hand2',
                      pady=2 if small else 3,
                      font=('Helvetica', 8 if small else 9))
        b.pack(fill='x', pady=1, side=side, **pack_kw)
        return b

    def _preset(self, keys):
        for k, v in self._codec_vars.items():
            v.set(k in keys)

    def _get_keys(self) -> list:
        sel = [k for k, v in self._codec_vars.items() if v.get()]
        for m in MANDATORY:
            if m not in sel:
                sel.append(m)
        return sel

    def _draw_chart(self):
        self._chart.delete('all')
        w = max(self._chart.winfo_width(), 400)
        h = 60
        hist = self._bitrate_history[-80:] 
        if not hist:
            self._chart.create_text(w // 2, h // 2, text="No data",
                                     fill=C['dim'], font=('Helvetica', 9))
            return

        mx = max(max(hist), self._max_kbps, 1)
        bar_w = max(w // max(len(hist), 1), 2)

        for i, val in enumerate(hist):
            x0 = i * bar_w
            bar_h = int((val / mx) * (h - 10))
            y0 = h - bar_h
            frac = val / mx
            color = C['green'] if frac < 0.5 else C['yellow'] if frac < 0.8 else C['red']
            self._chart.create_rectangle(x0, y0, x0 + bar_w - 1, h,
                                          fill=color, outline='')

        cur = hist[-1]
        self._chart.create_text(w - 4, 4, anchor='ne',
                                 text=f"{cur:.0f} kbps",
                                 fill=C['text'], font=('Helvetica', 8))

    def _poll(self):
        try:
            while True:
                ev = self._ev_q.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, ev):
        t = ev['type']

        if t == 'LOG':
            self._log(ev.get('text', ''), ev.get('level', 'normal'))
        elif t == 'DISCOVERABLE':
            self._hdr_status.config(text="◎ Discoverable")
            self._c_conn.config(text="Discoverable", fg=C['yellow'])
        elif t == 'CONNECTED':
            self._hdr_status.config(text="● Connected")
            self._c_conn.config(text="Connected ✓", fg=C['green'])
        elif t == 'STREAM_OPENED':
            codec = ev.get('codec', '?')
            info  = ev.get('info', {})
            cfg   = ev.get('cfg_hex', '')

            self._log(f"\n✅  STREAM OPENED  →  {codec}\n", 'stream')
            if cfg:
                self._log(f"  raw config: {cfg}", 'info')

            self._c_codec.config(text=codec, fg=C['green'])

            sr = info.get('sample_rate')
            self._c_sr.config(
                text=(f"{sr/1000:.1f} kHz" if sr and sr >= 1000 else (f"{sr} Hz" if sr else "—")),
                fg=C['text']
            )
            bd = info.get('bit_depth')
            self._c_bd.config(text=f"{bd}-bit" if bd else "—", fg=C['text'])

            mk = info.get('max_kbps')
            if mk:
                self._max_kbps = float(mk)
                self._c_br.config(text=f"≤{mk} kbps", fg=C['text'])
        elif t == 'STREAM_CLOSED':
            self._log(f"  stream closed: {ev.get('codec','?')}", 'warning')
            self._c_codec.config(text="—", fg=C['text'])
        elif t == 'BITRATE':
            kbps = ev.get('kbps', 0)
            self._bitrate_history.append(kbps)
            if len(self._bitrate_history) > 200:
                self._bitrate_history = self._bitrate_history[-200:]
            self._c_br.config(text=f"{kbps:.0f} kbps", fg=C['text'])
            self._draw_chart()
        elif t == 'DONE':
            self._running = False
            self._set_btns(running=False)
            self._hdr_status.config(text="● Idle")
            self._c_conn.config(text="—", fg=C['text'])
            self._log("\n[→] Session ended — resetting HCI...", 'warning')
            threading.Thread(target=self._do_reset, daemon=True).start()

    def _do_reset(self):
        reset_hci()
        self._ev_q.put({'type': 'LOG', 'text': "[✓] HCI ready.", 'level': 'info'})

    def _start(self):
        keys = self._get_keys()
        if not keys:
            messagebox.showwarning("No codecs", "Select at least one codec.")
            return
        self._running = True
        self._set_btns(running=True)
        self._clear_log()
        self._reset_cards()
        self._bitrate_history = []
        self._draw_chart()

        names = [CODECS[k][0] for k in keys]
        self._log(f"Starting session: {', '.join(names)}", 'info')

        self._worker = BenchWorker(self._ev_q, self._log)
        self._worker.start(keys, 'hci-socket:0', self._audio_var.get())

    def _stop(self):
        if self._worker:
            self._worker.stop()

    def _set_btns(self, running: bool):
        self._start_btn.config(
            state='disabled' if running else 'normal',
            bg=C['dim'] if running else C['accent']
        )
        self._stop_btn.config(
            state='normal' if running else 'disabled',
            bg=C['red'] if running else C['card'],
            fg='white' if running else C['dim']
        )

    def _reset_cards(self):
        self._hdr_status.config(text="● Running")
        for attr in ('_c_conn', '_c_codec', '_c_sr', '_c_bd', '_c_br'):
            getattr(self, attr).config(text="—", fg=C['text'])

    def _log(self, text, level='normal'):
        self._log_txt.config(state='normal')
        tag = level if level in ('info','success','warning','error','stream') else ''
        self._log_txt.insert('end', text + '\n', tag)
        self._log_txt.see('end')
        self._log_txt.config(state='disabled')

    def _clear_log(self):
        self._log_txt.config(state='normal')
        self._log_txt.delete('1.0', 'end')
        self._log_txt.config(state='disabled')

    def on_close(self):
        if self._worker:
            self._worker.stop()
            time.sleep(0.4)
        self.destroy()

def main():
    if os.geteuid() != 0:
        print("ERROR: This tool needs root to access the HCI socket.")
        print("  Run:  sudo $(which python3) codec_tester_gui.py")
        sys.exit(1)

    if 'DISPLAY' not in os.environ and 'WAYLAND_DISPLAY' not in os.environ:
        print("ERROR: No display found. Run from a desktop session.")
        sys.exit(1)

    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()

if __name__ == "__main__":
    main()