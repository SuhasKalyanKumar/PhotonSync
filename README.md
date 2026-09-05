# PhotonSync
### Ambient Screen & Voice-Reactive BLE Engine

PhotonSync is a lightweight Python utility that links your Windows PC to Bluetooth Low Energy (BLE) RGB LED strips. It syncs your lights in real-time with your screen colors or responds instantly to offline voice commands.

---

## Features

* **Real-Time Screen Sync:** Captures your monitor feed via `mss` and streams ambient color shifts to your LED strip.
* **Offline Voice Control:** Powered by **Vosk** and **Soundcard** for local, cloud-free speech recognition.
* **Smart BLE Auto-Discovery:** Automatically scans for and connects to popular LED controllers (like BLEDOM or Triones).
* **Catppuccin GUI:** A clean dark-mode dashboard with brightness sliders, telemetry logs, and toggles.

---

## Installation & Quickstart

```bash
# 1. Clone the repository
git clone https://github.com/SuhasKalyanKumar/PhotonSync.git
cd PhotonSync

# 2. Install the required dependencies
pip install -r requirements.txt

# 3. Run the application (the Vosk model will download automatically on first launch)
python main.py
