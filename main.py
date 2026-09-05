import asyncio
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.request
import zipfile
import numpy as np
import mss
from bleak import BleakClient, BleakScanner, BleakError

# =============================================================
# BRANDING & APP METADATA
# =============================================================
APP_NAME = "PhotonSync"
APP_SUBTITLE = "Ambient Screen & Voice-Reactive BLE Engine"
APP_VERSION = "v1.0.0"

# =============================================================
# AUTOMATIC VOSK MODEL DOWNLOADER
# =============================================================
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_ZIP = "model.zip"
MODEL_DIR = "model"
EXTRACTED_DIR_NAME = "vosk-model-small-en-us-0.15"

def download_vosk_model_if_missing():
    """Checks if the Vosk model exists locally; if not, downloads and extracts it automatically."""
    if not os.path.exists(MODEL_DIR):
        print(f"[{APP_NAME}] ⏳ Vosk model folder not found. Downloading automatically (~40MB)...")
        try:
            urllib.request.urlretrieve(MODEL_URL, MODEL_ZIP)
            
            print(f"[{APP_NAME}] 📦 Extracting model archive...")
            with zipfile.ZipFile(MODEL_ZIP, 'r') as zip_ref:
                zip_ref.extractall(".")
                
            if os.path.exists(EXTRACTED_DIR_NAME):
                if os.path.exists(MODEL_DIR):
                    import shutil
                    shutil.rmtree(MODEL_DIR)
                os.rename(EXTRACTED_DIR_NAME, MODEL_DIR)
                
            if os.path.exists(MODEL_ZIP):
                os.remove(MODEL_ZIP)
                
            print(f"[{APP_NAME}] ✅ Model setup complete and ready to use!")
        except Exception as e:
            print(f"[{APP_NAME}] ⚠️ Failed to automatically download model: {e}")

# Run the downloader before initializing anything else
download_vosk_model_if_missing()

# Optional Voice Recognition & Audio Libraries
try:
    import soundcard as sc
    from vosk import Model, KaldiRecognizer
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False

# -------------------------------------------------------------
# HIGH-DPI SCALING FIX (WINDOWS)
# -------------------------------------------------------------
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# -------------------------------------------------------------
# HARDWARE CONFIGURATION & GLOBAL STATES
# -------------------------------------------------------------
FALLBACK_CHARACTERISTIC_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
target_characteristic_uuid = None

connected_mac_address = None
ble_client = None
loop = None
brightness_factor = 1.0
power_state = True
is_running = True
is_blinking = False

app_mic_muted = False
sync_mode = True  # True = Screen Sync active | False = Fixed static color active

last_voice_cmd_time = 0.0
VOICE_COOLDOWN = 1.0

current_raw_rgb = [255, 255, 255]

COLOR_DICTIONARY = {
    "red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 0, 255],
    "yellow": [255, 255, 0], "cyan": [0, 255, 255], "magenta": [255, 0, 255],
    "white": [255, 255, 255], "orange": [255, 128, 0], "amber": [255, 191, 0],
    "gold": [255, 215, 0], "peach": [255, 203, 164], "pink": [255, 105, 180],
    "hot pink": [255, 20, 147], "purple": [128, 0, 128], "violet": [238, 130, 238],
    "lavender": [230, 230, 250], "indigo": [75, 0, 130], "sky blue": [135, 206, 235],
    "light blue": [173, 216, 230], "ice blue": [200, 235, 255], "navy": [0, 0, 128],
    "teal": [0, 128, 128], "turquoise": [64, 224, 208], "aqua": [0, 255, 255],
    "lime": [50, 205, 50], "mint": [152, 255, 152], "forest green": [34, 139, 34],
    "emerald": [80, 200, 120], "crimson": [220, 20, 60], "maroon": [128, 0, 0],
    "coral": [255, 127, 80], "brown": [139, 69, 19]
}

log_box = None
root_gui = None
brightness_slider = None
mic_btn = None

def log_msg(msg):
    """Appends live log updates directly to the terminal console and GUI."""
    print(f"[{APP_NAME}] > {msg}")
    
    if root_gui:
        def _update():
            if log_box and log_box.winfo_exists():
                log_box.config(state="normal")
                log_box.insert("end", f"> {msg}\n")
                log_box.see("end")
                log_box.config(state="disabled")
        root_gui.after(0, _update)

# -------------------------------------------------------------
# DYNAMIC GATT CHARACTERISTIC RESOLVER
# -------------------------------------------------------------
def discover_write_characteristic(client):
    """Scans client GATT services for a writable characteristic."""
    global target_characteristic_uuid
    
    for service in client.services:
        for char in service.characteristics:
            props = char.properties
            if "write" in props or "write-without-response" in props:
                target_characteristic_uuid = char.uuid
                log_msg(f"🎯 Dynamic Characteristic Resolved: {char.uuid}")
                return char.uuid

    log_msg(f"⚠️ Dynamic discovery failed. Using fallback: {FALLBACK_CHARACTERISTIC_UUID}")
    target_characteristic_uuid = FALLBACK_CHARACTERISTIC_UUID
    return FALLBACK_CHARACTERISTIC_UUID

# -------------------------------------------------------------
# BLE TRANSMITTER
# -------------------------------------------------------------
async def send_color(rgb, raw_cache=True, ignore_brightness=False):
    global current_raw_rgb
    if raw_cache:
        current_raw_rgb = [int(rgb[0]), int(rgb[1]), int(rgb[2])]

    if ble_client and ble_client.is_connected and target_characteristic_uuid:
        if not power_state and not is_blinking:
            payload = bytearray([0x7e, 0x07, 0x05, 0x03, 0x00, 0x00, 0x00, 0x00, 0xef])
        else:
            scale = 1.0 if ignore_brightness else brightness_factor
            r = int(np.clip(rgb[0] * scale, 0, 255))
            g = int(np.clip(rgb[1] * scale, 0, 255))
            b = int(np.clip(rgb[2] * scale, 0, 255))
            payload = bytearray([0x7e, 0x07, 0x05, 0x03, r, g, b, 0x00, 0xef])

        try:
            await ble_client.write_gatt_char(target_characteristic_uuid, payload, response=False)
        except Exception:
            pass

def trigger_realtime_brightness():
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(send_color(current_raw_rgb, raw_cache=False), loop)

# -------------------------------------------------------------
# ANIMATION SEQUENCES
# -------------------------------------------------------------
async def blink_power_on_sequence():
    global is_blinking
    is_blinking = True
    for color in [[255, 0, 0], [0, 255, 0], [0, 0, 255]]:
        await send_color(color, raw_cache=False, ignore_brightness=True)
        await asyncio.sleep(0.12)
    is_blinking = False

async def blink_power_off_sequence():
    global is_blinking
    is_blinking = True
    for color in [[0, 0, 255], [0, 255, 0], [255, 0, 0]]:
        await send_color(color, raw_cache=False, ignore_brightness=True)
        await asyncio.sleep(0.12)
    is_blinking = False
    await send_color([0, 0, 0], raw_cache=False, ignore_brightness=True)

# -------------------------------------------------------------
# VOICE ENGINE
# -------------------------------------------------------------
def voice_listener_worker():
    global power_state, brightness_factor, last_voice_cmd_time, sync_mode

    if not VOICE_AVAILABLE:
        log_msg("⚠️ Voice libraries missing. Speech engine disabled.")
        return

    if not os.path.exists("model"):
        log_msg("⚠️ Vosk 'model' folder missing. Voice listener aborted.")
        return

    try:
        sample_rate = 16000
        model = Model("model")

        valid_phrases = [
            "turn on", "turn off", "power on", "power off", "on", "off",
            "screen sync", "sync", "normal",
            "low brightness", "medium brightness", "high brightness", "low", "medium", "high"
        ] + list(COLOR_DICTIONARY.keys())

        grammar_json = json.dumps(valid_phrases)
        recognizer = KaldiRecognizer(model, sample_rate, grammar_json)

        physical_mics = sc.all_microphones(include_loopback=False)
        if not physical_mics:
            log_msg("⚠️ No physical microphone detected!")
            return
        mic = physical_mics[0]
        
        block_size = int(sample_rate * 0.25)

        with mic.recorder(samplerate=sample_rate, channels=1) as recorder:
            while is_running:
                data = recorder.record(numframes=block_size)

                if app_mic_muted:
                    time.sleep(0.05)
                    continue

                pcm_data = (data[:, 0] * 32767).astype(np.int16).tobytes()

                if recognizer.AcceptWaveform(pcm_data):
                    res = json.loads(recognizer.Result())
                    text = res.get("text", "").lower().strip()

                    if text:
                        log_msg(f'🎙️ [Voice Command]: "{text}"')
                        words = text.split()
                        now = time.time()

                        if now - last_voice_cmd_time >= VOICE_COOLDOWN:
                            if "on" in words or "turn on" in text or "power on" in text:
                                if not power_state:
                                    last_voice_cmd_time = now
                                    power_state = True
                                    sync_mode = True
                                    log_msg("⚡ Power ON (Screen Sync Active)")
                                    if loop and loop.is_running():
                                        asyncio.run_coroutine_threadsafe(blink_power_on_sequence(), loop)

                            elif "off" in words or "turn off" in text or "power off" in text:
                                if power_state:
                                    last_voice_cmd_time = now
                                    power_state = False
                                    log_msg("🌙 Power OFF")
                                    if loop and loop.is_running():
                                        asyncio.run_coroutine_threadsafe(blink_power_off_sequence(), loop)

                            elif "normal" in words or "sync" in words or "screen sync" in text:
                                last_voice_cmd_time = now
                                sync_mode = True
                                power_state = True
                                log_msg("🖥️ Resumed Screen Sync Mode")

                            elif "low" in words or "low brightness" in text:
                                last_voice_cmd_time = now
                                brightness_factor = 0.3
                                if brightness_slider:
                                    brightness_slider.set(30)
                                log_msg("💡 Brightness -> LOW (30%)")
                                trigger_realtime_brightness()

                            elif "medium" in words or "medium brightness" in text:
                                last_voice_cmd_time = now
                                brightness_factor = 0.6
                                if brightness_slider:
                                    brightness_slider.set(60)
                                log_msg("💡 Brightness -> MEDIUM (60%)")
                                trigger_realtime_brightness()

                            elif "high" in words or "high brightness" in text:
                                last_voice_cmd_time = now
                                brightness_factor = 1.0
                                if brightness_slider:
                                    brightness_slider.set(100)
                                log_msg("💡 Brightness -> HIGH (100%)")
                                trigger_realtime_brightness()

                            else:
                                matched_color = None
                                for c_name in sorted(COLOR_DICTIONARY.keys(), key=len, reverse=True):
                                    if c_name in text:
                                        matched_color = c_name
                                        break

                                if matched_color and power_state:
                                    last_voice_cmd_time = now
                                    sync_mode = False  
                                    rgb = COLOR_DICTIONARY[matched_color]
                                    log_msg(f"🎨 Color Changed -> {matched_color.title()}")

                                    if loop and loop.is_running():
                                        asyncio.run_coroutine_threadsafe(send_color(rgb), loop)

                time.sleep(0.01)

    except Exception as e:
        log_msg(f"⚠️ Voice Engine Error: {e}")

# -------------------------------------------------------------
# PIXEL AVERAGING ENGINE
# -------------------------------------------------------------
def compute_screen_average_rgb(img_bytes, width, height):
    img_np = np.frombuffer(img_bytes, dtype=np.uint8).reshape((height, width, 4))
    sub_sample = img_np[::16, ::16, :3]
    
    if sub_sample.size == 0:
        return np.array([0.0, 0.0, 0.0])

    b_avg = np.mean(sub_sample[:, :, 0])
    g_avg = np.mean(sub_sample[:, :, 1])
    r_avg = np.mean(sub_sample[:, :, 2])

    return np.array([r_avg, g_avg, b_avg])

async def screen_sync_loop():
    current_rgb = np.array([0.0, 0.0, 0.0])
    last_sent_rgb = np.array([-1, -1, -1])
    last_tx_time = 0.0
    TX_INTERVAL = 0.020 

    with mss.mss() as sct:
        monitor = sct.monitors[1]

        while is_running and power_state and sync_mode and not is_blinking:
            try:
                sct_img = sct.grab(monitor)
                target_rgb = compute_screen_average_rgb(sct_img.raw, sct_img.width, sct_img.height)

                current_rgb = current_rgb + 0.75 * (target_rgb - current_rgb)
                int_rgb = current_rgb.astype(int)

                now = time.monotonic()
                if (now - last_tx_time >= TX_INTERVAL) and np.max(np.abs(int_rgb - last_sent_rgb)) >= 1:
                    last_sent_rgb = int_rgb
                    last_tx_time = now
                    await send_color(int_rgb)

            except Exception:
                pass

            await asyncio.sleep(0.005)

# -------------------------------------------------------------
# BLE AUTO-SCAN & CONNECTION ENGINE
# -------------------------------------------------------------
async def find_rgb_strip():
    log_msg("🔍 Scanning BLE spectrum for LED controllers...")

    try:
        devices = await BleakScanner.discover(timeout=3.0)
        for dev in devices:
            name = (dev.name or "").lower()
            if any(keyword in name for keyword in ["elk", "lotus", "led", "bledom", "triones", "strip", "light", "ble"]):
                log_msg(f"✅ Found Supported Target: {dev.name} ({dev.address})")
                return dev.address

        if devices:
            log_msg(f"⚠️ Fallback target selected: {devices[0].address}")
            return devices[0].address
    except Exception as e:
        log_msg(f"⚠️ Bluetooth Adapter Error: {e}")

    return None

async def async_main():
    global ble_client, loop, connected_mac_address, sync_mode
    loop = asyncio.get_running_loop()

    threading.Thread(target=voice_listener_worker, daemon=True).start()

    while is_running:
        try:
            target_mac = await find_rgb_strip()

            if not target_mac:
                log_msg("❌ No BLE light strip found. Retrying in 5s...")
                await asyncio.sleep(5)
                continue

            connected_mac_address = target_mac
            log_msg(f"Initiating link with {target_mac}...")

            async with BleakClient(target_mac) as client:
                ble_client = client
                sync_mode = True
                
                discover_write_characteristic(client)
                log_msg("⚡ Hardware linked successfully!")

                await blink_power_on_sequence()

                while client.is_connected and is_running:
                    if power_state and sync_mode and not is_blinking:
                        await screen_sync_loop()
                    elif power_state and not sync_mode and not is_blinking:
                        await asyncio.sleep(0.1)
                    elif not power_state and not is_blinking:
                        await send_color([0, 0, 0], raw_cache=False)
                        await asyncio.sleep(0.5)
                    else:
                        await asyncio.sleep(0.05)

        except (BleakError, Exception) as e:
            if is_running:
                log_msg(f"⚠️ Link disrupted ({e}). Reconnecting in 3s...")
                await asyncio.sleep(3)

def start_async_thread():
    asyncio.run(async_main())

# -------------------------------------------------------------
# DASHBOARD INTERFACE (CATPPUCCIN THEME)
# -------------------------------------------------------------
def create_gui():
    global log_box, root_gui, brightness_slider, mic_btn

    root = tk.Tk()
    root_gui = root
    root.title(APP_NAME)
    
    root.geometry("540x740")
    root.configure(bg="#1e1e2e")
    root.resizable(False, False)

    main_frame = tk.Frame(root, bg="#1e1e2e")
    main_frame.pack(fill="both", expand=True, padx=20, pady=15)

    # BRAND HEADER
    header_frame = tk.Frame(main_frame, bg="#1e1e2e")
    header_frame.pack(fill="x", pady=(0, 10))

    title_label = tk.Label(header_frame, text=f"✨ {APP_NAME.upper()}", font=("Segoe UI", 16, "bold"), fg="#cba6f7", bg="#1e1e2e")
    title_label.pack(anchor="w")

    subtitle_label = tk.Label(header_frame, text=APP_SUBTITLE, font=("Segoe UI", 8), fg="#a6adc8", bg="#1e1e2e")
    subtitle_label.pack(anchor="w")

    # VOICE COMMAND GUIDE
    guide_card = tk.Frame(main_frame, bg="#181825", highlightbackground="#313244", highlightthickness=1)
    guide_card.pack(fill="x", pady=(0, 10), ipady=6)

    tk.Label(guide_card, text="🎙️ Voice Engine Syntax", font=("Segoe UI", 10, "bold"), fg="#cba6f7", bg="#181825").pack(anchor="w", padx=12, pady=(2, 4))

    instructions = [
        ("Power:", '"On" / "Off"'),
        ("Modes:", '"Sync" / "Normal"'),
        ("Levels:", '"Low" / "Medium" / "High"'),
        ("Colors:", '"Red", "Cyan", "Purple", "Pink"...')
    ]

    for label, cmd in instructions:
        row = tk.Frame(guide_card, bg="#181825")
        row.pack(fill="x", padx=12, pady=1)
        tk.Label(row, text=f"• {label} ", font=("Segoe UI", 9, "bold"), fg="#a6adc8", bg="#181825").pack(side="left")
        tk.Label(row, text=cmd, font=("Segoe UI", 9), fg="#cdd6f4", bg="#181825").pack(side="left")

    # HARDWARE CONTROL CARD
    ctrl_card = tk.Frame(main_frame, bg="#181825", highlightbackground="#313244", highlightthickness=1)
    ctrl_card.pack(fill="x", pady=4, ipady=6)

    btn_frame = tk.Frame(ctrl_card, bg="#181825")
    btn_frame.pack(fill="x", padx=12)

    power_btn = tk.Button(
        btn_frame, text="⚡ POWER STATE: ON", font=("Segoe UI", 9, "bold"),
        bg="#a6e3a1", fg="#11111b", activebackground="#a6e3a1", activeforeground="#11111b",
        bd=0, relief="flat", cursor="hand2"
    )

    def toggle_power():
        global power_state, sync_mode
        power_state = not power_state
        if power_state:
            sync_mode = True
            power_btn.config(bg="#a6e3a1", fg="#11111b", text="⚡ POWER STATE: ON")
            log_msg("Power Restored -> Resumed Screen Sync")
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(blink_power_on_sequence(), loop)
        else:
            power_btn.config(bg="#f38ba8", fg="#11111b", text="🌙 POWER STATE: OFF")
            log_msg("Power Suspended")
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(blink_power_off_sequence(), loop)

    power_btn.config(command=toggle_power)
    power_btn.pack(fill="x", ipady=4, pady=(0, 6))

    mic_btn = tk.Button(
        btn_frame, text="🎙️ VOICE RECOGNITION: ACTIVE", font=("Segoe UI", 9, "bold"),
        bg="#89b4fa", fg="#11111b", activebackground="#89b4fa", activeforeground="#11111b",
        bd=0, relief="flat", cursor="hand2"
    )

    def toggle_app_mic():
        global app_mic_muted
        app_mic_muted = not app_mic_muted
        if app_mic_muted:
            mic_btn.config(bg="#6c7086", fg="#cdd6f4", text="🎙️ VOICE RECOGNITION: PAUSED")
            log_msg("🔇 Voice Commands Muted (Application Level)")
        else:
            mic_btn.config(bg="#89b4fa", fg="#11111b", text="🎙️ VOICE RECOGNITION: ACTIVE")
            log_msg("🎙️ Voice Commands Resumed")

    mic_btn.config(command=toggle_app_mic)
    mic_btn.pack(fill="x", ipady=4)

    # BRIGHTNESS CARD
    bright_card = tk.Frame(main_frame, bg="#181825", highlightbackground="#313244", highlightthickness=1)
    bright_card.pack(fill="x", pady=4, ipady=6)

    bright_header = tk.Frame(bright_card, bg="#181825")
    bright_header.pack(fill="x", padx=12, pady=(2, 0))

    tk.Label(bright_header, text="💡 Master Brightness", font=("Segoe UI", 9, "bold"), fg="#cba6f7", bg="#181825").pack(side="left")
    bright_val_lbl = tk.Label(bright_header, text="100%", font=("Segoe UI", 9, "bold"), fg="#89b4fa", bg="#181825")
    bright_val_lbl.pack(side="right")

    def update_brightness(val):
        global brightness_factor
        pct = int(float(val))
        brightness_factor = pct / 100.0
        bright_val_lbl.config(text=f"{pct}%")
        trigger_realtime_brightness()

    brightness_slider = tk.Scale(
        bright_card, from_=0, to=100, orient="horizontal", command=update_brightness,
        bg="#181825", fg="#cdd6f4", highlightthickness=0, troughcolor="#313244",
        activebackground="#cba6f7", bd=0, showvalue=False
    )
    brightness_slider.set(100)
    brightness_slider.pack(fill="x", padx=12, pady=(0, 2))

    # CONSOLE LOG
    console_frame = tk.Frame(main_frame, bg="#181825", highlightbackground="#313244", highlightthickness=1)
    console_frame.pack(fill="both", expand=True, pady=(6, 0))

    tk.Label(console_frame, text="💻 System Telemetry", font=("Segoe UI", 9, "bold"), fg="#a6adc8", bg="#181825").pack(anchor="w", padx=10, pady=(4, 2))

    log_box = tk.Text(
        console_frame, bg="#11111b", fg="#a6e3a1", font=("Consolas", 8),
        bd=0, relief="flat", state="disabled", wrap="word"
    )
    log_box.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def on_closing():
        global is_running
        is_running = False
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    threading.Thread(target=start_async_thread, daemon=True).start()
    root.mainloop()

if __name__ == "__main__":
    create_gui()