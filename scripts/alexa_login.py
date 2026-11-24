"""One-time Amazon sign-in so Karen can control your Alexa smart home devices.

You sign in on Amazon's own login page. It is passed through a small relay on this PC (AlexaPy's login
proxy, the same one Home Assistant's Alexa Media Player uses), so your password goes only to Amazon and is
never saved. Karen keeps the resulting login cookies and tokens in %LOCALAPPDATA%\\Karen\\alexa (outside the
project, never committed). Run it again if Karen says the Alexa sign-in expired.

    python scripts/alexa_login.py
"""
import asyncio
import json
import os
import secrets
import sys
import webbrowser

import httpx
import yaml
from aiohttp import web

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STORE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Karen", "alexa")
PORT = 8775


async def main():
    from alexapy import AlexaAPI, AlexaProxy
    sys.path.insert(0, "servers/alexa")
    from region import RegionLogin  # signs in on amazon.<region>, not always amazon.com
    cfg = (yaml.safe_load(open("config.yaml", encoding="utf-8")) or {}).get("alexa", {})
    region = cfg.get("region", "amazon.com")
    os.makedirs(STORE, exist_ok=True)
    email = input("Your Amazon account email: ").strip()
    if "@" not in email:
        sys.exit("That doesn't look like an email address.")
    uuid = secrets.token_hex(16)
    login = RegionLogin(url=region, email=email, password="", outputpath=lambda p: os.path.join(STORE, p),
                       uuid=uuid, oauth_login=True)
    proxy = AlexaProxy(login, f"http://127.0.0.1:{PORT}/auth/proxy")
    proxy.session_factory = lambda: httpx.AsyncClient(follow_redirects=False,
                                                      timeout=httpx.Timeout(30.0, read=120.0))
    done = asyncio.Event()

    async def finished(request):
        done.set()
        return web.Response(text="Signed in to Amazon. You can close this tab and go back to the terminal.")

    app = web.Application()
    app.router.add_route("*", "/auth/proxy", proxy.all_handler)
    app.router.add_route("*", "/auth/proxy/{tail:.*}", proxy.all_handler)
    app.router.add_get("/auth/done", finished)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    url = proxy.access_url().with_query({"config_flow_id": "karen", "callback_url": f"http://127.0.0.1:{PORT}/auth/done"})
    login.proxy_url = url
    login._session.cookie_jar.clear()
    print(f"\nOpening Amazon sign-in ({region}) in your browser. Sign in there (including the OTP if Amazon asks).")
    print(f"If no tab opens, go to: {url}\n")
    webbrowser.open(str(url))
    await done.wait()

    if not await login.test_loggedin():
        await runner.cleanup()
        sys.exit("Sign-in didn't complete. Run this script again.")
    await login.finalize_login()
    await login.save_cookiefile()
    account = {"email": login.email, "uuid": uuid, "region": region,
               "oauth": {"access_token": login.access_token, "refresh_token": login.refresh_token,
                         "expires_in": login.expires_in, "mac_dms": login.mac_dms}}
    with open(os.path.join(STORE, "account.json"), "w", encoding="utf-8") as f:
        json.dump(account, f)

    echoes = await AlexaAPI.get_devices(login) or []
    homes = await AlexaAPI.get_network_details(login) or []
    print(f"\nSigned in. Echo devices: {', '.join(e.get('accountName', '?') for e in echoes if e.get('deviceFamily') in ('ECHO', 'KNIGHT', 'ROOK')) or 'none found'}")
    print(f"Smart home devices Alexa knows: {len(homes)}")
    for d in homes[:40]:
        print(f"  - {d.get('friendlyName')}  ({', '.join(d.get('applianceTypes') or [])})")
    await login.close()
    await runner.cleanup()
    print("\nDone. Tell Claude it worked.")


if __name__ == "__main__":
    asyncio.run(main())
