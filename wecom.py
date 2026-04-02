import base64
import hashlib
import secrets
import struct
import threading
import time
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlsplit

import requests
from Crypto.Cipher import AES

WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_SEND_MESSAGE_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
BLOCK_SIZE = 32


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    if pad_len == 0:
        pad_len = BLOCK_SIZE
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty data")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > BLOCK_SIZE:
        raise ValueError("invalid padding")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid padding bytes")
    return data[:-pad_len]


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = sorted([str(token or ""), str(timestamp or ""), str(nonce or ""), str(encrypted or "")])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _xml_text(root: ET.Element, tag: str) -> str:
    value = root.findtext(tag)
    return str(value or "").strip()


def _mask_wecom_value(value: str | None, head: int = 3, tail: int = 3) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= head + tail:
        return "*" * len(raw)
    return f"{raw[:head]}***{raw[-tail:]}"


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = str(token or "")
        self.corp_id = str(corp_id or "")
        key = str(encoding_aes_key or "").strip()
        if len(key) != 43:
            raise ValueError("企业微信 EncodingAESKey 长度不对")
        self.aes_key = base64.b64decode(key + "=")
        self.iv = self.aes_key[:16]

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
        expected = _sha1_signature(self.token, timestamp, nonce, encrypted)
        return secrets.compare_digest(expected, str(msg_signature or ""))

    def decrypt(self, encrypted: str) -> str:
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        decoded = base64.b64decode(str(encrypted or ""))
        plain = _pkcs7_unpad(cipher.decrypt(decoded))
        if len(plain) < 20:
            raise ValueError("企业微信密文长度异常")
        msg_len = struct.unpack("!I", plain[16:20])[0]
        xml_bytes = plain[20:20 + msg_len]
        receive_id = plain[20 + msg_len:].decode("utf-8")
        if self.corp_id and receive_id != self.corp_id:
            raise ValueError("企业微信 receive_id/corp_id 不匹配")
        return xml_bytes.decode("utf-8")

    def encrypt(self, plain_text: str, nonce: Optional[str] = None, timestamp: Optional[str] = None) -> dict:
        nonce = str(nonce or secrets.token_hex(8))
        timestamp = str(timestamp or int(time.time()))
        plain_bytes = str(plain_text or "").encode("utf-8")
        raw = secrets.token_bytes(16) + struct.pack("!I", len(plain_bytes)) + plain_bytes + self.corp_id.encode("utf-8")
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        encrypted = base64.b64encode(cipher.encrypt(_pkcs7_pad(raw))).decode("utf-8")
        signature = _sha1_signature(self.token, timestamp, nonce, encrypted)
        xml = (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
        return {
            "encrypt": encrypted,
            "msg_signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "xml": xml,
        }

    def decrypt_echostr(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        if not self.verify_signature(msg_signature, timestamp, nonce, echostr):
            raise ValueError("企业微信签名校验失败")
        return self.decrypt(echostr)

    def decrypt_message_xml(self, body: str, msg_signature: str, timestamp: str, nonce: str) -> dict:
        root = ET.fromstring(body)
        encrypted = _xml_text(root, "Encrypt")
        if not encrypted:
            raise ValueError("企业微信消息缺少 Encrypt")
        if not self.verify_signature(msg_signature, timestamp, nonce, encrypted):
            raise ValueError("企业微信签名校验失败")
        plain_xml = self.decrypt(encrypted)
        plain_root = ET.fromstring(plain_xml)
        data = {child.tag: (child.text or "") for child in plain_root}
        data["_xml"] = plain_xml
        return data


class WeComClient:
    def __init__(
        self,
        corp_id: str,
        agent_id: str | int,
        secret: str,
        timeout: int | float = 10,
        connect_timeout: int | float | None = 3.05,
        read_timeout: int | float | None = None,
        api_base_url: str | None = None,
    ):
        self.corp_id = str(corp_id or "")
        self.agent_id = int(agent_id)
        self.secret = str(secret or "")
        self.timeout = float(timeout)
        self.connect_timeout = float(connect_timeout) if connect_timeout is not None else None
        self.read_timeout = float(read_timeout) if read_timeout is not None else self.timeout
        self.request_timeout = (
            (self.connect_timeout, self.read_timeout)
            if self.connect_timeout is not None and self.read_timeout is not None
            else self.timeout
        )
        self.api_base_url = self._normalize_api_base_url(api_base_url)
        self.token_url = self._build_api_url("/cgi-bin/gettoken")
        self.send_message_url = self._build_api_url("/cgi-bin/message/send")
        self._token = None
        self._token_expire_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_api_base_url(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            raise ValueError("企业微信 API base URL 不合法")
        path = (parts.path or "").rstrip("/")
        if path.endswith("/cgi-bin/message/send"):
            path = path[: -len("/cgi-bin/message/send")]
        elif path.endswith("/cgi-bin/gettoken"):
            path = path[: -len("/cgi-bin/gettoken")]
        return f"{parts.scheme}://{parts.netloc}{path}"

    def _build_api_url(self, path: str) -> str:
        normalized_path = "/" + str(path or "").lstrip("/")
        if self.api_base_url:
            return f"{self.api_base_url}{normalized_path}"
        if normalized_path == "/cgi-bin/gettoken":
            return WECOM_TOKEN_URL
        if normalized_path == "/cgi-bin/message/send":
            return WECOM_SEND_MESSAGE_URL
        raise ValueError(f"unsupported wecom api path: {normalized_path}")

    def _fetch_access_token(self) -> str:
        masked_corp = _mask_wecom_value(self.corp_id)
        try:
            resp = requests.get(
                self.token_url,
                params={"corpid": self.corp_id, "corpsecret": self.secret},
                timeout=self.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"企业微信获取 access_token 请求失败: corp_id={masked_corp} error={exc}") from exc
        except ValueError as exc:
            body = (getattr(resp, "text", "") or "")[:300]
            raise RuntimeError(f"企业微信获取 access_token 响应非 JSON: corp_id={masked_corp} body={body}") from exc

        if data.get("errcode") != 0:
            errcode = data.get("errcode")
            errmsg = data.get("errmsg") or data
            raise RuntimeError(f"企业微信获取 access_token 失败: corp_id={masked_corp} errcode={errcode} errmsg={errmsg}")

        token = str(data.get("access_token") or "")
        expires_in = int(data.get("expires_in") or 7200)
        if not token:
            raise RuntimeError(f"企业微信 access_token 为空: corp_id={masked_corp}")
        self._token = token
        self._token_expire_at = time.time() + max(60, expires_in - 120)
        return token

    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if force_refresh or not self._token or time.time() >= self._token_expire_at:
                return self._fetch_access_token()
            return self._token

    def send_text(self, to_user: str, content: str) -> dict:
        payload = {
            "touser": str(to_user or ""),
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": str(content or "")},
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 0,
        }
        masked_user = _mask_wecom_value(payload["touser"])

        def do_send(access_token: str) -> dict:
            try:
                resp = requests.post(
                    self.send_message_url,
                    params={"access_token": access_token},
                    json=payload,
                    timeout=self.request_timeout,
                )
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                raise RuntimeError(f"企业微信发消息请求失败: touser={masked_user} agentid={self.agent_id} error={exc}") from exc
            except ValueError as exc:
                body = (getattr(resp, "text", "") or "")[:300]
                raise RuntimeError(f"企业微信发消息响应非 JSON: touser={masked_user} agentid={self.agent_id} body={body}") from exc

        token = self.get_access_token()
        data = do_send(token)
        if data.get("errcode") == 42001:
            data = do_send(self.get_access_token(force_refresh=True))

        if data.get("errcode") != 0:
            errcode = data.get("errcode")
            errmsg = data.get("errmsg") or data
            invalid_user_hint = " (请检查 touser 是否是企业微信可见成员/UserID)" if errcode in {60111, 81013} else ""
            raise RuntimeError(
                f"企业微信发消息失败: touser={masked_user} agentid={self.agent_id} errcode={errcode} errmsg={errmsg}{invalid_user_hint}"
            )
        return data


def build_passive_text_reply(to_user: str, from_user: str, content: str) -> str:
    timestamp = int(time.time())
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )
