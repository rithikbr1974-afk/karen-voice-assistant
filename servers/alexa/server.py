"""Karen's smart home control through Amazon Alexa, as an MCP server (stdio).

Uses AlexaPy, the unofficial library behind Home Assistant's Alexa Media Player (Amazon has no official API
for controlling Alexa devices from your own code), so it can break when Amazon changes its site.
Sign in once with scripts/alexa_login.py; the login lives in %LOCALAPPDATA%\\Karen\\alexa.

Safety: lights, fans and plugs switch directly. High-power appliances (by Alexa device type or by name, see
config.yaml -> alexa.high_power) can only be switched with power_appliance, and free-form commands and
routines go through alexa_command / run_routine; Karen asks for a spoken yes before all three.
Every action is logged to logs/home_actions.log.
"""
import datetime
import difflib
import json
import os
import time
from types import SimpleNamespace

import sys

import yaml
from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for region.py

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("alexa", {})
STORE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Karen", "alexa")
LOG = os.path.join(ROOT, "logs", "home_actions.log")
HIGH_POWER_TYPES = set(CFG.get("high_power", {}).get("types", []))
HIGH_POWER_WORDS = [w.lower() for w in CFG.get("high_power", {}).get("names", [])]
NOT_SIGNED_IN = "Alexa isn't signed in (or the sign-in expired). Tell the user to run scripts/alexa_login.py."

mcp = MCPServer("alexa")
state = {"login": None, "devices": {}, "devices_at": 0.0, "echo": None}


def log(action, detail=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {action:<16} {detail}\n")


async def login():
    """Signed-in AlexaLogin, reusing the saved cookies. None if sign-in is needed."""
    from region import RegionLogin as AlexaLogin  # signs in on amazon.<region>, not always amazon.com
    if state["login"] is not None:
        return state["login"]
    path = os.path.join(STORE, "account.json")
    if not os.path.exists(path):
        return None
    acct = json.load(open(path, encoding="utf-8"))
    lg = AlexaLogin(url=acct["region"], email=acct["email"], password="", outputpath=lambda p: os.path.join(STORE, p),
                    uuid=acct["uuid"], oauth=acct.get("oauth", {}), oauth_login=True)
    await lg.login(cookies=await lg.load_cookie())
    if not lg.status.get("login_successful"):
        await lg.close()
        return None
    await lg.save_cookiefile()
    acct["oauth"] = {"access_token": lg.access_token, "refresh_token": lg.refresh_token,
                     "expires_in": lg.expires_in, "mac_dms": lg.mac_dms}
    json.dump(acct, open(path, "w", encoding="utf-8"))
    state["login"] = lg
    return lg


async def devices():
    """Smart home devices Alexa knows: lowercase name -> appliance dict (refreshed every 6 hours)."""
    from alexapy import AlexaAPI
    if state["devices"] and time.time() - state["devices_at"] < 6 * 3600:
        return state["devices"]
    lg = await login()
    if lg is None:
        return None
    found = await AlexaAPI.get_network_details(lg) or []
    state["devices"] = {d["friendlyName"].lower(): d for d in found if d.get("friendlyName") and d.get("isEnabled", True)}
    state["devices_at"] = time.time()
    return state["devices"]


async def find(name):
    """(device dict, error text). Fuzzy match so "bedroom light" finds "Bedroom Light 1"."""
    devs = await devices()
    if devs is None:
        return None, NOT_SIGNED_IN
    name = name.lower().strip()
    names = list(devs)
    hit = name if name in devs else next(iter(sorted((n for n in names if name in n), key=len)), None)
    hit = hit or next(iter(difflib.get_close_matches(name, names, n=1, cutoff=0.55)), None)
    if not hit:
        return None, f"No Alexa device called '{name}'. Known: {', '.join(d['friendlyName'] for d in devs.values())[:600]}"
    return devs[hit], ""


def high_power(dev):
    types = set(dev.get("applianceTypes") or [])
    name = dev.get("friendlyName", "").lower()
    return bool(types & HIGH_POWER_TYPES) or any(w in name.split() or (" " in w and w in name) for w in HIGH_POWER_WORDS)


def entity(dev):
    return dev.get("entityId") or dev.get("applianceId")


def describe_state(raw):
    """Turn Alexa's /api/phoenix/state answer into a short sentence."""
    parts = []
    for ds in (raw or {}).get("deviceStates", []):
        for cap in ds.get("capabilityStates", []):
            c = json.loads(cap) if isinstance(cap, str) else cap
            name, value = c.get("name"), c.get("value")
            if name == "powerState":
                parts.append(f"power {str(value).lower()}")
            elif name == "brightness":
                parts.append(f"brightness {value}%")
            elif name in ("temperature", "targetSetpoint", "upperSetpoint", "lowerSetpoint") and isinstance(value, dict):
                parts.append(f"{'set to' if 'Setpoint' in name else 'temperature'} {value.get('value')} {str(value.get('scale', '')).title()}")
            elif name in ("thermostatMode", "mode") and value:
                parts.append(f"mode {str(value).lower()}")
            elif name == "connectivity" and isinstance(value, dict) and value.get("value") != "OK":
                parts.append("offline")
    errors = (raw or {}).get("errors") or []
    if not parts and errors:
        return "Alexa couldn't read its state: " + ", ".join(str(e.get("code", e)) for e in errors)[:200]
    return ", ".join(parts) or "no state reported"


async def set_power(dev, on, brightness=None):
    from alexapy import AlexaAPI
    lg = await login()
    if lg is None:
        return NOT_SIGNED_IN
    result = await AlexaAPI.set_light_state(lg, entity(dev), power_on=on, brightness=brightness) or {}
    errors = result.get("errors") or []
    log("power", f"{dev['friendlyName']} -> {'on' if on else 'off'}" + (f" {brightness}%" if brightness is not None else "")
        + (f" ERRORS {errors}" if errors else ""))
    if errors:
        return f"Alexa refused: {', '.join(str(e.get('code', e)) for e in errors)[:200]}"
    return f"{dev['friendlyName']} turned {'on' if on else 'off'}" + (f" at {brightness}%" if brightness is not None else "") + "."


async def echo():
    """The Echo that runs free-form commands (config alexa.echo, else the first online Echo)."""
    from alexapy import AlexaAPI
    if state["echo"]:
        return state["echo"]
    lg = await login()
    if lg is None:
        return None
    echoes = [d for d in (await AlexaAPI.get_devices(lg) or [])
              if d.get("deviceFamily") in ("ECHO", "KNIGHT", "ROOK") and d.get("online", True)]
    want = (CFG.get("echo") or "").lower()
    pick = next((d for d in echoes if want and want in d.get("accountName", "").lower()), echoes[0] if echoes else None)
    if pick:
        state["echo"] = AlexaAPI(SimpleNamespace(device_serial_number=pick["serialNumber"], _device_type=pick["deviceType"],
                                                 _device_family=pick.get("deviceFamily"), _locale=pick.get("locale", "en-IN"),
                                                 _cluster_members=pick.get("clusterMembers", [])), lg)
    return state["echo"]


# ---------------------------------------------------------------- tools
@mcp.tool()
async def list_devices() -> str:
    """List the smart home devices Alexa knows, by type."""
    devs = await devices()
    if devs is None:
        return NOT_SIGNED_IN
    by_type = {}
    for d in devs.values():
        kind = ((d.get("applianceTypes") or ["OTHER"])[0]).lower().replace("_", " ")
        by_type.setdefault(kind, []).append(d["friendlyName"] + (" (high-power)" if high_power(d) else ""))
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(by_type.items())) or "Alexa has no smart home devices."


@mcp.tool()
async def device_status(name: str) -> str:
    """Is a device on or off, its brightness, temperature or mode. Example: "is the fan on?"."""
    from alexapy import AlexaAPI
    dev, err = await find(name)
    if err:
        return err
    raw = await AlexaAPI.get_entity_state(await login(), entity_ids=[entity(dev)])
    return f"{dev['friendlyName']}: {describe_state(raw)}"


@mcp.tool()
async def switch(name: str, on: bool) -> str:
    """Turn a light, fan, plug or similar small device on or off."""
    dev, err = await find(name)
    if err:
        return err
    if high_power(dev):
        return f"{dev['friendlyName']} is a high-power appliance: use power_appliance instead."
    return await set_power(dev, on)


@mcp.tool()
async def set_brightness(name: str, level: int) -> str:
    """Dim or brighten a light, 0-100."""
    dev, err = await find(name)
    if err:
        return err
    return await set_power(dev, level > 0, brightness=max(0, min(100, level)))


@mcp.tool()
async def power_appliance(name: str, on: bool) -> str:
    """Turn a high-power appliance on or off (AC, heater, geyser, iron, oven, washing machine...)."""
    dev, err = await find(name)
    if err:
        return err
    return await set_power(dev, on)


@mcp.tool()
async def alexa_command(text: str) -> str:
    """Say anything to Alexa through the Echo, exactly as the user would, for what the other tools can't do.
    Example: "set the bedroom AC to 24 degrees", "set the fan to speed 3". Alexa may answer out loud on the Echo."""
    api = await echo()
    if api is None:
        return NOT_SIGNED_IN if await login() is None else "No online Echo device found."
    await api.run_custom(text)
    log("alexa_command", text)
    return f"Sent to Alexa: {text}"


@mcp.tool()
async def run_routine(name: str) -> str:
    """Run one of the user's Alexa routines by its name or trigger phrase, e.g. "good night"."""
    api = await echo()
    if api is None:
        return NOT_SIGNED_IN if await login() is None else "No online Echo device found."
    await api.run_routine(name)
    log("run_routine", name)
    return f"Ran the Alexa routine '{name}'."


if __name__ == "__main__":
    mcp.run()
