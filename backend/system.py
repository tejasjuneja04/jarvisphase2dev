"""
Real machine status for the HUD and for questions like "how's my battery?".
Uses psutil when installed; otherwise falls back to what the OS offers. Anything unknown is None,
never a made-up number.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys


def _battery_pmset():
    if sys.platform != "darwin":
        return None, None
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"(\d+)%;\s*(charging|discharging|charged|finishing charge|AC attached)", out)
        if m:
            return int(m.group(1)), m.group(2) in ("charging", "charged", "finishing charge", "AC attached")
    except Exception:
        pass
    return None, None


def snapshot() -> dict:
    out = {"cpu": None, "mem": None, "battery": None, "charging": None}
    try:
        import psutil
        out["cpu"] = round(psutil.cpu_percent(interval=None))
        out["mem"] = round(psutil.virtual_memory().percent)
        b = psutil.sensors_battery()
        if b is not None:
            out["battery"], out["charging"] = round(b.percent), bool(b.power_plugged)
    except Exception:
        try:
            load = os.getloadavg()[0] / (os.cpu_count() or 1) * 100
            out["cpu"] = int(max(0, min(100, load)))
        except Exception:
            pass
    if out["battery"] is None:
        out["battery"], out["charging"] = _battery_pmset()
    return out


def _osascript(script: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=4)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def set_volume(level: int) -> bool:
    level = max(0, min(100, int(level)))
    return _osascript(f"set volume output volume {level}") is not None


def get_volume() -> int | None:
    v = _osascript("output volume of (get volume settings)")
    return int(v) if v and v.isdigit() else None


def set_mute(muted: bool) -> bool:
    return _osascript(f"set volume {'with' if muted else 'without'} output muted") is not None


def handle(text: str, title: str = "sir") -> str | None:
    """Natural-language system commands. Returns a reply or None when the text is not for this module."""
    low = text.lower().strip().rstrip("?.! ")

    if re.search(r"\b(battery|charge level|how much charge)\b", low) and not re.search(r"\b(chart|plot|data)\b", low):
        s = snapshot()
        if s["battery"] is None:
            return f"I can't read a battery on this machine, {title}. It may be a desktop."
        state = "and charging" if s["charging"] else "on battery"
        return f"Battery is at {s['battery']} percent, {state}, {title}."

    if re.match(r"^(system|computer|mac|machine)\s+(status|stats|health|report)$|^how(?:'s| is| are)\s+(?:my|the)\s+"
                r"(computer|mac|system|machine)(?:\s+doing)?$|^status report$|^system check$", low):
        s = snapshot()
        parts = []
        if s["cpu"] is not None:
            parts.append(f"processor at {s['cpu']} percent")
        if s["mem"] is not None:
            parts.append(f"memory at {s['mem']} percent")
        if s["battery"] is not None:
            parts.append(f"battery at {s['battery']} percent{' and charging' if s['charging'] else ''}")
        return (f"All systems are running, {title}: " + ", ".join(parts) + ".") if parts else \
               f"I can't read the system sensors here, {title}."

    m = re.search(r"\bvolume\b.*?\b(\d{1,3})\b|\b(?:set|make)\b.*?\b(\d{1,3})\s*(?:%|percent)\b.*\bvolume\b", low)
    if m and ("volume" in low):
        level = int(m.group(1) or m.group(2))
        if sys.platform != "darwin":
            return f"Volume control works on macOS only, {title}."
        return f"Volume set to {max(0, min(100, level))} percent, {title}." if set_volume(level) else \
               f"I couldn't change the volume, {title}."

    m = re.match(r"^(?:turn|make|bring)?\s*(?:the\s+)?(?:volume\s+)?(up|down)(?:\s+(?:the\s+)?volume)?$|^volume\s+(up|down)$|"
                 r"^(?:turn|make)\s+(?:it|the volume)\s+(up|down)$", low)
    if m:
        direction = next(g for g in m.groups() if g)
        if sys.platform != "darwin":
            return f"Volume control works on macOS only, {title}."
        cur = get_volume()
        if cur is None:
            return f"I couldn't read the current volume, {title}."
        new = max(0, min(100, cur + (12 if direction == "up" else -12)))
        return f"Volume {direction} to {new} percent, {title}." if set_volume(new) else f"I couldn't change the volume, {title}."

    if low in ("mute", "mute the volume", "mute sound", "silence"):
        return f"Muted, {title}." if set_mute(True) else f"I couldn't mute the system, {title}."
    if low in ("unmute", "unmute the volume", "unmute sound"):
        return f"Sound restored, {title}." if set_mute(False) else f"I couldn't unmute the system, {title}."
    return None
