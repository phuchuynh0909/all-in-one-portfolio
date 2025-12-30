import math
import random
from hashlib import md5
import requests
import json
import time
from Cryptodome.Cipher import AES
import base64

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
    headers = {
        'authority': 'wichart.vn',
        'host': 'wichart.vn',
        'accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Cookie': 'deviceToken=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1bmlxdWVJRCI6IjFiNTFhNjA4ODQyZjc2NjJjYmM2MGFkOGZjNDI3ZmFjIiwiZXhwaXJlcyI6IjIwMjYtMTItMzBUMDM6NDY6NTkuODU4WiIsImlhdCI6MTc2NzA2NjQxOX0.Cav-d5UMmAnMnjlFAgmtQk58HRcjZbM1HBanQCqhn9s',
        'Nonce': nonce, 'Origin': 'https://wichart.vn', 'Referer': 'https://wichart.vn/login',
        'sec-ch-ua': '"Google Chrome";v="117", "Not;A=Brand";v="8", "Chromium";v="117"',
        'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"', 'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin', 'sec-gpc': '1',
        'Sign': getSign(signData), 'Sign-Token': 'ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX', 'Stime': str(now),
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
        'v': 'v1',
        'visit-id': "089be3fa-3082-4dea-b940-67563d6d6144"
    }

    response = requests.request("POST", url, headers=headers, data=json.dumps(payload))
    data = response.json()
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

