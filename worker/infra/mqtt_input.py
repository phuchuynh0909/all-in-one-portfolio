import os, asyncio, math, time, ssl
from paho.mqtt.client import Client as MQTTClient
from random import randint
import paho.mqtt.client as mqtt
from bytewax.inputs import DynamicSource, StatelessSourcePartition, StatefulSourcePartition
from typing import List, Union
from requests import post, get
from dotenv import load_dotenv
from bytewax.inputs import FixedPartitionedSource, StatefulSourcePartition, batch_async

CLIENT_ID_PREFIX = "dnse-price-json-mqtt-ws-sub-"

load_dotenv()
username = os.getenv("ENTRADE_USER") # Email hoặc số điện thoại đăng kí tài khoản
password = os.getenv("ENTRADE_PASSWORD") # Mật khẩu đăng nhập tài khoản

# Nhập thông tin vào đây (nếu có), và comment đoạn try...except bên dưới
investor_id = None
token = None

def authenticate(username, password):
    try:
        url = "https://api.dnse.com.vn/user-service/api/auth"
        _json = {
            "username": username,
            "password": password
        }
        response = post(url, json=_json)
        response.raise_for_status()

        print("Authentication successful!")
        return response.json().get("token")

    except Exception as e:
        print(f"Authentication failed: {e}")
        return None

def get_investor_info(token = None):
    try:
        url = f"https://api.dnse.com.vn/user-service/api/me"
        headers = {
            "authorization": f"Bearer {token}"
        }

        response = get(url, headers=headers)
        response.raise_for_status()
        investor_info = response.json()
        print("Get investor info successful!")
        return investor_info

    except Exception as e:
        print(f"Failed to get investor info: {e}")
        return None

# ---------- Bytewax input (MQTT) ----------
class MqttPartition(StatelessSourcePartition):
    
    def __init__(self, host: str, port: int, topics: Union[str, List[str]]):
        self._host = host
        self._port = port
        self._topics = topics
        self._q = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._client_id = f"{CLIENT_ID_PREFIX}{randint(1000, 2000)}"

        try: # Có thể comment nếu có thông tin
            token = authenticate(username, password)
            if token is not None:
                investor_info = get_investor_info(token=token)
                if investor_info is not None:
                    investor_id = str(investor_info["investorId"])
                else:
                    raise Exception("Failed to get investor info.")
            else:
                raise Exception("Authentication failed.")

        except Exception as e:
            print(f"Error: {e}")
            exit()
            
        self._client = MQTTClient(
            mqtt.CallbackAPIVersion.VERSION2,
            self._client_id,
            protocol=mqtt.MQTTv5,
            transport="websockets"
        )

        # Set credentials
        self._client.username_pw_set(investor_id, token)

        # SSL/TLS configuration (since it's wss://)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE) # Bỏ qua kiểm tra SSL
        self._client.tls_insecure_set(True) # Cho phép kết nối với chứng chỉ self-signed
        self._client.ws_set_options(path="/wss")
        self._client.enable_logger()


        self._client.on_message = self._on_message
        self._client.connect(self._host, self._port, keepalive=1200)
        
        # Wildcards allowed, e.g., ticks/+/+
        for t in self._topics:
            print(f"Subscribing to topic: {t}")
            self._client.subscribe(t, qos=1)
        self._client.loop_start()

    def _on_message(self, client, userdata, msg):
        self._q.put_nowait((msg.topic, msg.payload))

    def next_batch(self, sched=None):
        # Drain up to N messages without blocking
        batch = []
        try:
            while True:
                batch.append(self._q.get_nowait())
                if len(batch) >= 1024:
                    break
        except asyncio.QueueEmpty:
            pass
        return batch

    def close(self):
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass

class MqttSource(DynamicSource):
    def __init__(self, host: str, port: int, topics: List[str]):
        self._host = host
        self._port = port
        self._topics = topics

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        # MVP: one partition per worker (single worker is fine)
        return MqttPartition(self._host, self._port, self._topics)