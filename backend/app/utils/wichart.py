import math
import os
import random
from hashlib import md5
import requests
import json
import time
from Cryptodome.Cipher import AES
import base64

SIGN_TOKEN = "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX"

# The device-token check compares the user-agent against the session the
# token was minted in: a short or mismatched UA gets "Không thể xác thực
# thiết bị" (400) even with a valid token. Keep this in step with
# DEFAULT_DEVICE_TOKEN whenever a new token is captured.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# The macro endpoints that widata.vn (wichart's public data front-end) calls
# authenticate on a *device* token alone — no account login, no Bearer header.
# Grab a fresh one from any widata.vn request (the `device-token` header, which
# is also the `deviceToken` cookie) and set WICHART_DEVICE_TOKEN; the default
# below is the token captured on 2026-08-17, whose embedded expiry is
# 2026-12-31.
DEFAULT_DEVICE_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1bmlxdWVJRCI6IjkxMDFjZWU3OGJhMjY0MzgyMzczNTUxYjYzODI5MDdlIiwiZXhwaXJlcyI6"
    "IjIwMjYtMTItMzFUMDg6NDE6MzEuODM2WiIsImlhdCI6MTc2NzE3MDQ5MX0."
    "j5MD2OUwYfFVm75XzDXFEg6kzATAYa1zXAx8h3gyB7k"
)

MACRO_URL = "https://wichart.vn/wichartapi/macro/templates/data"

# Government bond yields used to live on the `hst_bond` template. That table
# still answers, but every row now comes back with a null value; the series moved
# to the numeric template id 410 ("Bond yield Vietnam <n>Y"), which is what
# widata.vn itself reads. Same four maturities, same [timestamp_ms, value] rows.
MACRO_BOND_TABLE = os.getenv("WICHART_MACRO_TABLE", "410")
MACRO_BOND_DIMS = ["vietnam_1y", "vietnam_3y", "vietnam_5y", "vietnam_10y"]


def getNonce():
    return str(int(math.floor((random.random() + math.floor(9 * random.random() + 1)) * math.pow(10, 19))))


def getSign(sign):
    signatureEncode = "".join([str(key) + str(value) for key, value in sorted(sign.items())])
    return md5(signatureEncode.encode('utf-8')).hexdigest()


def getToken():
    url = "https://wichart.vn/wichartapi/wichart/taikhoan/dangnhap"
    now = int(time.time() * 1000)
    payload = {
        "email": "phuc991994@gmail.com",
        "password": "kyostyle1"
    }
    nonce = getNonce()
    signData = {
        "email": payload['email'],
        "nonce": nonce,
        'password': payload['password'],
        "sign-token": "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX",
        "stime": now,
        "v": "v1"
    }
    # The device cookie comes from getDeviceToken() (env-overridable) rather than
    # being pinned here: login rejects a stale one outright, and this file used to
    # carry its own older copy that had gone bad while the macro flow's token was
    # still good.
    device_token = getDeviceToken()
    headers = {
        'authority': 'wichart.vn',
        'host': 'wichart.vn',
        'accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Cookie': 'deviceToken=' + device_token,
        'device-token': device_token,
        'Nonce': nonce, 'Origin': 'https://wichart.vn', 'Referer': 'https://wichart.vn/login',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"', 'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin', 'sec-gpc': '1',
        'Sign': getSign(signData), 'Sign-Token': SIGN_TOKEN, 'Stime': str(now),
        'user-agent': BROWSER_USER_AGENT,
        'v': 'v1',
        'visit-id': "089be3fa-3082-4dea-b940-67563d6d6144"
    }

    response = requests.request("POST", url, headers=headers, data=json.dumps(payload), timeout=30)
    data = response.json()
    if 'token' not in data:
        raise RuntimeError(
            f"wichart login failed: {data.get('message', data)}. A rejected device is usually a "
            f"stale WICHART_DEVICE_TOKEN — capture a fresh one from a widata.vn request."
        )
    return data['token']


def getHeaders(token, nonce, hashCode, stime):
    return {
        'authority': 'wichart.vn', 'accept': 'application/json, text/plain, */*',
        'authorization': 'Bearer ' + token, 'content-type': 'application/json',
        'cookie': 'deviceToken=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1bmlxdWVJRCI6ImFlYWRiNGQ0NTI5MDRjYWFmYTkzMjZhYjQ1OTUyYzY4IiwiZXhwaXJlcyI6IjIwMjUtMTItMTVUMDU6MDk6MTAuMTIzWiIsImlhdCI6MTczNDIzOTM1MH0.mp6nwgEg1jIvsLk2rj4y8KwomS8H9oEk5AONNvmc2Pc; wid=zZZ87Fb9f21VeYwiLfMq; wtoken=' + token,
        'origin': 'https://wichart.vn', 'referer': 'https://wichart.vn/report',
        'sec-ch-ua': '"Not/A)Brand";v="99", "Google Chrome";v="115", "Chromium";v="115"',
        'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty', 'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        'v': 'v1', 'nonce': nonce, 'sign': hashCode, 'sign-token': "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX", 'stime': str(stime),
    }

def getDeviceToken():
    return os.getenv("WICHART_DEVICE_TOKEN") or DEFAULT_DEVICE_TOKEN


def getDeviceHeaders(nonce, hashCode, stime, device_token=None):
    """Headers for the widata.vn flow: device token, no account login.

    Deliberately separate from ``getHeaders``: that one is the logged-in
    wichart.vn variant (Bearer + wtoken cookie + wichart.vn origin), and the
    macro template endpoints reject it — they are called from widata.vn and
    check the device token instead. The signature scheme is the same, so the
    caller builds ``hashCode`` with ``getSign`` either way.
    """
    token = device_token or getDeviceToken()
    return {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9,vi;q=0.8',
        'cookie': 'deviceToken=' + token,
        'device-token': token,
        'origin': 'https://widata.vn', 'referer': 'https://widata.vn/',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty', 'sec-fetch-mode': 'cors', 'sec-fetch-site': 'cross-site',
        'user-agent': BROWSER_USER_AGENT,
        'v': 'v1', 'nonce': nonce, 'sign': hashCode, 'sign-token': SIGN_TOKEN, 'stime': str(stime),
    }


def fetchMacroSeries(table_name, column_name):
    """One macro series from the template endpoint.

    Returns the decrypted ``data`` object: ``data['data']`` is the
    ``[timestamp_ms, value]`` history, alongside metadata (chartTitle, unit).
    The endpoint signs the query — md5 over the query params plus
    nonce/stime/v and the shared sign-token, sorted by key — and authenticates
    on the device token (see getDeviceHeaders) rather than an account login.
    """
    query_params = {
        "table_name": table_name,
        "column_name": column_name,
        "time_frame": "daily",
        "value_type": "value",
        "version": "2",
    }
    nonce = getNonce()
    stime = int(time.time() * 1000)
    sign_data = dict(query_params, nonce=nonce, stime=stime, v="v1")
    sign_data["sign-token"] = SIGN_TOKEN

    response = requests.get(
        MACRO_URL,
        params=query_params,
        headers=getDeviceHeaders(nonce, getSign(sign_data), stime),
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if "enc" not in body:
        raise RuntimeError(f"{table_name}/{column_name}: unexpected response {body}")
    return json.loads(decrypt(body["enc"]))["data"]


def fetchMacroFrame(dims=None, table_name=None, start_date="2008-01-01", log=print):
    """Several macro series as one tidy frame: ``value, date, dim_name, key``.

    ``key`` (``<dim>_<YYYY-MM-DD>``) is the merge key of the ``wichart_macro``
    Delta table, so the column set here is exactly that table's schema. Rows
    with a null value are dropped: the endpoint serves the whole history on
    every call and the downstream merge only fills gaps, so a null written
    today would never be corrected once the series is backfilled.
    """
    import pandas as pd

    dims = list(dims or MACRO_BOND_DIMS)
    table_name = table_name or MACRO_BOND_TABLE

    frames = []
    for dim in dims:
        data = fetchMacroSeries(table_name, dim)
        df = pd.DataFrame(data['data'], columns=['timestamp', 'value'])
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['dim_name'] = dim
        df['key'] = df['dim_name'] + '_' + df['date'].dt.strftime('%Y-%m-%d')
        if log:
            log(f"{dim}: {len(df)} rows ({data.get('chartTitle')}, unit {data.get('unit')})")
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=['value', 'date', 'dim_name', 'key'])

    total_df = pd.concat(frames, ignore_index=True)
    if start_date:
        total_df = total_df[total_df['date'] >= start_date]
    total_df = total_df.dropna(subset=['value'])
    total_df = total_df.drop(columns=['timestamp'])
    return total_df[['value', 'date', 'dim_name', 'key']].reset_index(drop=True)


SECTOR_PRICE_URL = "https://wichart.vn/wichartapi/sector/nganh/gia"

# Levels 3 and 4 reuse the same small integer ids (id 26 exists in both), and
# padding them to four digits would collide with the level 1/2 ICB codes already
# in ``ohlc_eod`` (``0001``, ``0500``). The level prefix is what keeps all four
# apart, so every reader must build the symbol through here.
def sectorSymbol(level, sector_id):
    """Pseudo-symbol a sector's index series is stored under in ``ohlc_eod``."""
    if int(level) in (1, 2):
        return f"{int(sector_id):04d}"
    return f"SECTOR{int(level)}_{int(sector_id)}"


def fetchSectorSeries(sector_id, level, from_date, to_date, key="close"):
    """One sector's index history: a list of ``{id, name, data}`` items.

    ``data`` is ``[timestamp_ms, value]`` rows. Signed like the other wichart
    endpoints — md5 over the query params plus nonce/stime/v and the shared
    sign-token, sorted by key — but note the signature uses ``listID`` while the
    query string sends ``listID[]``. Values are absolute index levels and do not
    re-base with the requested window.

    Authenticates on the device token alone, like the macro endpoints — this one
    validates the signature and nothing else. Responses were byte-identical to
    the logged-in variant across both levels, the full id range and history back
    to 2008, so the account login it originally used bought nothing and is not
    worth its fragility.
    """
    nonce = getNonce()
    stime = int(time.time() * 1000)
    sign_data = {
        "from": from_date,
        "key": key,
        "listID": int(sector_id),
        "nonce": nonce,
        "sign-token": SIGN_TOKEN,
        "stime": stime,
        "to": to_date,
        "type": int(level),
        "v": "v1",
    }
    url = (
        f"{SECTOR_PRICE_URL}?key={key}&type={int(level)}"
        f"&listID[]={int(sector_id)}&from={from_date}&to={to_date}"
    )
    response = requests.get(
        url,
        headers=getDeviceHeaders(nonce, getSign(sign_data), stime),
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if "enc" not in body:
        raise RuntimeError(f"sector {level}/{sector_id}: unexpected response {body}")
    return json.loads(decrypt(body["enc"]))


def fetchSectorFrame(sector_ids_by_level, from_date, to_date, key="close", log=print):
    """Sector index history as one tidy frame: ``date, symbol, value, level, sector_id, sector_name``.

    ``sector_ids_by_level`` maps a level (3 or 4) to the wichart list ids to
    pull; one request per sector, because the endpoint returns a single series
    per call. A sector that errors is logged and skipped rather than failing the
    whole crawl — with ~100 sequential requests, one bad id should not cost the
    other ninety-nine.

    No account login: this rides the device token like ``fetchMacroFrame``, so
    ``WICHART_DEVICE_TOKEN`` is the only credential to refresh.
    """
    import pandas as pd

    frames = []
    failures = 0

    for level, sector_ids in sector_ids_by_level.items():
        for sector_id in sector_ids:
            try:
                items = fetchSectorSeries(sector_id, level, from_date, to_date, key=key)
            except Exception as exc:
                failures += 1
                if log:
                    log(f"  sector {level}/{sector_id}: {exc}")
                continue

            for item in items or []:
                rows = item.get("data") or []
                if not rows:
                    continue
                df = pd.DataFrame(rows, columns=["timestamp", "value"])
                df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
                df["level"] = int(level)
                df["sector_id"] = int(item.get("id", sector_id))
                df["sector_name"] = item.get("name")
                df["symbol"] = sectorSymbol(level, df["sector_id"].iloc[0])
                frames.append(df.drop(columns=["timestamp"]))

    columns = ["date", "symbol", "value", "level", "sector_id", "sector_name"]
    if not frames:
        if log:
            log(f"No sector series fetched ({failures} failure(s))")
        return pd.DataFrame(columns=columns)

    total = pd.concat(frames, ignore_index=True).dropna(subset=["value"])
    if log:
        log(
            f"Sectors: {len(total):,} rows across {total['symbol'].nunique()} series"
            + (f", {failures} failure(s)" if failures else "")
        )
    return total[columns].reset_index(drop=True)


def decrypt(encrypted_text):
    passphrase = "ZmRvaWFmaGRpc2ZoaWRzZHNoa2RoaW9zZGZoc2E=".encode()
    encrypted = base64.b64decode(encrypted_text)
    assert encrypted[0:8] == b"Salted__"
    salt = encrypted[8:16]
    key_iv = bytes_to_key(passphrase, salt, 32 + 16)
    key = key_iv[:32]
    iv = key_iv[32:]
    aes = AES.new(key, AES.MODE_CBC, iv)
    return unpad(aes.decrypt(encrypted[16:]))

def unpad(data):
    return data[:-(data[-1] if type(data[-1]) == int else ord(data[-1]))]

def bytes_to_key(data, salt, output=48):
    # extended from https://gist.github.com/gsakkis/4546068
    assert len(salt) == 8, len(salt)
    data += salt
    key = md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = md5(key + data).digest()
        final_key += key
    return final_key[:output]

