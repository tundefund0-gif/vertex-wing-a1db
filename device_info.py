#!/data/data/com.termux/files/usr/bin/python
"""Simple script to display Android device info using Termux:API."""
import subprocess
import json
import sys

def get_termux_json(cmd):
    """Run a termux command and return parsed JSON."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except Exception as e:
        return None

def main():
    print("=" * 50)
    print("📱  Android Device Info")
    print("=" * 50)

    # Battery info
    battery = get_termux_json(["termux-battery-status"])
    if battery:
        pct = battery.get("percentage", "?")
        status = battery.get("status", "?")
        temp = battery.get("temperature", "?")
        print(f"\n🔋 Battery:  {pct}%  |  Status: {status}  |  Temp: {temp}°C")

    # WiFi info
    wifi = get_termux_json(["termux-wifi-connectioninfo"])
    if wifi:
        ssid = wifi.get("ssid", "?")
        ip = wifi.get("ip", "?")
        rssi = wifi.get("rssi", "?")
        print(f"📶 WiFi:     {ssid}")
        print(f"   IP:       {ip}")
        print(f"   Signal:   {rssi} dBm")

    # Telephony info
    tele = get_termux_json(["termux-telephony-deviceinfo"])
    if tele:
        net = tele.get("network_operator", "?")
        country = tele.get("network_country_iso", "?")
        sim = tele.get("sim_operator", "?")
        print(f"📡 Network:  {net} ({country})")
        print(f"   SIM:      {sim}")

    # System info
    import platform, os
    print(f"\n💻 System:   {platform.system()} {platform.release()}")
    print(f"   Python:   {platform.python_version()}")
    print(f"   Device:   {platform.machine()}")

    # Disk space
    stat = os.statvfs("/")
    free_gb = (stat.f_bavail * stat.f_bsize) / (1024**3)
    total_gb = (stat.f_blocks * stat.f_bsize) / (1024**3)
    used_gb = total_gb - (stat.f_bfree * stat.f_bsize) / (1024**3)
    print(f"💾 Storage:  {used_gb:.1f}G used / {total_gb:.1f}G total")
    print(f"   Free:     {free_gb:.1f}G")

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_sec = float(f.read().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        print(f"⏱️  Uptime:   {days}d {hours}h {mins}m")
    except:
        pass

    print("\n" + "=" * 50)
    print("✅ Info fetched successfully!")
    print("=" * 50)

if __name__ == "__main__":
    main()
