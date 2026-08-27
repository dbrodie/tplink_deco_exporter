import asyncio
import base64
import binascii
import hashlib
import json
import logging
import math
import re
import secrets
import ssl
from typing import Any
from urllib.parse import quote_plus

import aiohttp
import async_timeout
from aiohttp.hdrs import CONTENT_TYPE, COOKIE, SET_COOKIE
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .exceptions import (
    EmptyDataException,
    ForbiddenException,
    LoginForbiddenException,
    LoginInvalidException,
    TimeoutException,
    UnexpectedApiException,
)

# Default timeout settings
DEFAULT_TIMEOUT_ERROR_RETRIES = 1
DEFAULT_TIMEOUT_SECONDS = 30

AES_KEY_BYTES = 16
MIN_AES_KEY = 10 ** (AES_KEY_BYTES - 1)
MAX_AES_KEY = (10**AES_KEY_BYTES) - 1

PKCS1_v1_5_HEADER_BYTES = 11

_LOGGER: logging.Logger = logging.getLogger(__name__)


def byte_len(n: int) -> int:
    return (int(math.log2(n)) + 8) >> 3


def decode_name_with_fallback(name: str):
    original = name
    try:
        name = base64.b64decode(name)
        return name.decode()
    except (binascii.Error, UnicodeError, ValueError) as err:
        _LOGGER.debug(
            "Client/device name was not base64 encoded: %s", type(err).__name__
        )
        return original


def rsa_encrypt(n: int, e: int, plaintext: bytes) -> str:
    """
    RSA encrypts plaintext. TP-Link breaks the plaintext down into blocks and concatenates the output.
    :param n: The RSA public key's n value
    :param e: The RSA public key's e value
    :param plaintext: The data to encrypt
    :return: RSA encrypted ciphertext
    """
    public_key = RSA.construct((n, e)).publickey()
    encryptor = PKCS1_v1_5.new(public_key)
    block_size = byte_len(n)
    bytes_per_block = block_size - PKCS1_v1_5_HEADER_BYTES

    encrypted_text = ""
    text_bytes = len(plaintext)
    index = 0
    while index < text_bytes:
        content_num_bytes = min(bytes_per_block, text_bytes - index)
        content = plaintext[index : index + content_num_bytes]
        encrypted_text += encryptor.encrypt(content).hex()
        index += content_num_bytes

    return encrypted_text


def aes_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """
    AES-CBC encrypt with PKCS #7 padding. This matches the AES options on TP-Link routers.
    :param key: The AES key
    :param iv: The AES IV
    :param plaintext: Data to encrypt
    :return: Ciphertext
    """
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    plaintext_bytes: bytes = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext_bytes) + encryptor.finalize()
    return ciphertext


def aes_decrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """
    AES-CBC decrypt with PKCS #7 padding.
    :param key: The AES key
    :param iv: The AES IV
    :param plaintext: Data to encrypt
    :return: Ciphertext
    """
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    ciphertext = decryptor.update(plaintext) + decryptor.finalize()
    return ciphertext


def check_data_error_code(context, data):
    error_code = data.get("error_code") or data.get("errorcode")
    if error_code:
        if error_code == "timeout":
            raise TimeoutException(f'{context} response error_code="timeout"')

        _LOGGER.debug("%s error_code=%s", context, error_code)
        raise UnexpectedApiException(f"{context} error_code={error_code}")


class TplinkDecoApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool,
        timeout_error_retries: int = DEFAULT_TIMEOUT_ERROR_RETRIES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._session = session
        self._timeout_error_retries = timeout_error_retries
        self._timeout_seconds = timeout_seconds
        self._auth_errors = 0

        self._aes_key = None
        self._aes_key_bytes = None
        self._aes_iv = None
        self._aes_iv_bytes = None

        self._password_rsa_n = None
        self._password_rsa_e = None
        self._sign_rsa_n = None
        self._sign_rsa_e = None

        self._login_future = None
        self._seq = None
        self._stok = None
        self._cookie = None

        if verify_ssl:
            self._ssl_context = None
        else:
            context = ssl.create_default_context()
            context.set_ciphers("DEFAULT")
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self._ssl_context = context

    async def async_request(
        self,
        path: str,
        form: str,
        operation: str = "read",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._async_call_with_retry(
            self._async_request, path, form, operation, params
        )

    async def _async_request(self, path, form, operation, params):
        await self.async_login_if_needed()
        context = f"{path}/{form}:{operation}"
        payload: dict[str, Any] = {"operation": operation}
        if params is not None:
            payload["params"] = params
        response_json = await self._async_post(
            context,
            f"{self._host}/cgi-bin/luci/;stok={self._stok}/admin/{path}",
            params={"form": form},
            data=self._encode_payload(payload),
        )
        data = self._decrypt_data(context, response_json["data"])
        check_data_error_code(context, data)
        result = data.get("result", {})
        if not isinstance(result, dict):
            raise UnexpectedApiException(f"{context} result is not an object")
        return result

    async def async_list_devices(self) -> list[dict[str, Any]]:
        result = await self.async_request("device", "device_list")
        devices = result.get("device_list", [])
        for device in devices:
            if device.get("custom_nickname"):
                device["custom_nickname"] = decode_name_with_fallback(
                    device["custom_nickname"]
                )
        return devices

    async def async_list_clients(self, deco_mac: str) -> list[dict[str, Any]]:
        result = await self.async_request(
            "client", "client_list", params={"device_mac": deco_mac}
        )
        clients = result.get("client_list", [])
        for client in clients:
            if client.get("name"):
                client["name"] = decode_name_with_fallback(client["name"])
        return clients

    async def async_build_log(self, level: int) -> dict[str, Any]:
        return await self.async_request(
            "log_export", "feedback_log", "build", {"level": level}
        )

    async def async_log_page(self, index: int, limit: int) -> dict[str, Any]:
        return await self.async_request(
            "log_export", "feedback_log", "read", {"index": index, "limit": limit}
        )

    def _generate_aes_key_and_iv(self):
        # TPLink requires key and IV to be a 16 digit number (no leading 0s)
        self._aes_key = secrets.randbelow(MAX_AES_KEY - MIN_AES_KEY) + MIN_AES_KEY
        self._aes_iv = secrets.randbelow(MAX_AES_KEY - MIN_AES_KEY) + MIN_AES_KEY
        self._aes_key_bytes = str(self._aes_key).encode("utf-8")
        self._aes_iv_bytes = str(self._aes_iv).encode("utf-8")

    # Fetch password RSA keys
    async def _async_fetch_keys(self):
        context = "Fetch keys"
        response_json = await self._async_post(
            context,
            f"{self._host}/cgi-bin/luci/;stok=/login",
            params={"form": "keys"},
            data=json.dumps({"operation": "read"}),
        )

        try:
            keys = response_json["result"]["password"]
            self._password_rsa_n = int(keys[0], 16)
            self._password_rsa_e = int(keys[1], 16)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.error("%s parse response error=%s", context, type(err).__name__)
            raise

    # Fetch sign RSA keys and seq no
    async def _async_fetch_auth(self):
        context = "Fetch auth"
        response_json = await self._async_post(
            context,
            f"{self._host}/cgi-bin/luci/;stok=/login",
            params={"form": "auth"},
            data=json.dumps({"operation": "read"}),
        )

        try:
            auth_result = response_json["result"]
            auth_key = auth_result["key"]
            self._sign_rsa_n = int(auth_key[0], 16)
            self._sign_rsa_e = int(auth_key[1], 16)
            self._seq = auth_result["seq"]
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.error("%s parse response error=%s", context, type(err).__name__)
            raise

    async def async_login_if_needed(self):
        if self._seq is None or self._stok is None or self._cookie is None:
            await self.async_login()

    async def async_login(self):
        if self._login_future is not None:
            await self._login_future
            return

        self._login_future = asyncio.get_running_loop().create_future()
        try:
            await self._async_login()
            self._login_future.set_result(True)
        except Exception as err:
            self._login_future.set_exception(err)
            raise
        finally:
            # Await future to suppress future exception was never retrieved error
            try:
                await self._login_future
            except Exception:  # noqa: BLE001,S110 - future exception was already propagated
                pass
            self._login_future = None

    async def _async_login(self):
        if self._aes_key is None:
            self._generate_aes_key_and_iv()
        if self._password_rsa_n is None:
            await self._async_fetch_keys()
        if self._seq is None:
            await self._async_fetch_auth()

        password_encrypted = rsa_encrypt(
            self._password_rsa_n, self._password_rsa_e, self._password.encode()
        )

        login_payload = {
            "params": {"password": password_encrypted},
            "operation": "login",
        }
        context = "Login"
        try:
            response_json = await self._async_post(
                context,
                f"{self._host}/cgi-bin/luci/;stok=/login",
                params={"form": "login"},
                data=self._encode_payload(login_payload),
            )
        except ForbiddenException as err:
            raise LoginForbiddenException(
                "Login auth error. Likely caused by logging in with admin account on another device."
                " See https://github.com/amosyuen/ha-tplink-deco#manager-account."
            ) from err

        data = self._decrypt_data(context, response_json["data"])
        error_code = data.get("error_code")
        result = data.get("result")
        if error_code != 0:
            if error_code == -5002:
                self.clear_auth()
                attempts = (result or {}).get("attemptsAllowed", "unknown")
                raise LoginInvalidException(attempts)
            raise UnexpectedApiException(f"Login error code={error_code}")
        check_data_error_code(context, data)

        try:
            self._stok = result["stok"]
        except Exception as err:
            _LOGGER.error("%s parse response error=%s", context, type(err).__name__)
            raise UnexpectedApiException from err

        if self._cookie is None:
            raise UnexpectedApiException(
                "Login response did not have a Set-Cookie header"
            )

        # Login success
        self._auth_errors = 0

    async def _async_post(
        self,
        context: str,
        url: str,
        params: dict[str:Any],
        data: Any,
    ) -> dict:
        headers = {CONTENT_TYPE: "application/json"}
        if self._cookie is not None:
            headers[COOKIE] = self._cookie
        try:
            async with async_timeout.timeout(self._timeout_seconds):
                response = await self._session.post(
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    ssl=self._ssl_context,
                )
                response.raise_for_status()

                cookie = response.headers.get(SET_COOKIE)
                if cookie is not None:
                    match = re.search(r"(sysauth=[a-f0-9]+)", cookie)
                    if match:
                        self._cookie = match.group(1)

                # Sometimes server responses with incorrect content type, so disable the check
                response_json = await response.json(content_type=None)
                if "error_code" in response_json:
                    error_code = response_json.get("error_code")

                    if error_code != 0 and error_code != "":
                        _LOGGER.debug("%s error_code=%s", context, error_code)
                        raise UnexpectedApiException(f"{context} error: {error_code}")

                return response_json
        except asyncio.TimeoutError as err:
            _LOGGER.debug(
                "%s timed out",
                context,
            )
            raise TimeoutException from err
        except aiohttp.ClientResponseError as err:
            _LOGGER.error(
                "%s client response error status=%s",
                context,
                err.status,
            )
            if err.status == 401:
                self.clear_auth()
                raise
            if err.status == 403:
                self.clear_auth()
                message = f"{context} forbidden"
                raise ForbiddenException(message) from err
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError) as err:
            # Clear auth in case deco rebooted and auth is invalid
            self.clear_auth()
            _LOGGER.error(
                "%s connection error: %s",
                context,
                type(err).__name__,
            )
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error(
                "%s client error: %s",
                context,
                type(err).__name__,
            )
            raise

    def _encode_payload(self, payload: Any):
        data = self._encode_data(payload)
        sign = self._encode_sign(len(data))
        # Must URI encode data after calculating data length
        payload = f"sign={sign}&data={quote_plus(data)}"
        return payload

    def _encode_sign(self, data_len: int):
        if self._seq is None:
            self.clear_auth()
            message = "_seq is None"
            raise EmptyDataException(message)
        seq_with_data_len = self._seq + data_len
        auth_hash = hashlib.md5(
            f"{self._username}{self._password}".encode()
        ).hexdigest()
        sign_text = (
            f"k={self._aes_key}&i={self._aes_iv}&h={auth_hash}&s={seq_with_data_len}"
        )
        sign = rsa_encrypt(self._sign_rsa_n, self._sign_rsa_e, sign_text.encode())
        return sign

    def _encode_data(self, payload: Any):
        payload_json = json.dumps(payload, separators=(",", ":"))

        data_encrypted = aes_encrypt(
            self._aes_key_bytes, self._aes_iv_bytes, payload_json.encode()
        )
        data = base64.b64encode(data_encrypted).decode()
        return data

    def clear_auth(self):
        _LOGGER.debug("clear_auth")
        self._seq = None
        self._stok = None
        self._cookie = None

    def _decrypt_data(self, context: str, data: str):
        if data == "":
            self.clear_auth()
            message = f"{context} data is empty"
            raise EmptyDataException(message)

        try:
            data_decoded = base64.b64decode(data)
            data_decrypted = aes_decrypt(
                self._aes_key_bytes, self._aes_iv_bytes, data_decoded
            )
            # Remove the PKCS #7 padding
            num_padding_bytes = int(data_decrypted[-1])
            data_decrypted = data_decrypted[:-num_padding_bytes].decode()
            data_json = json.loads(data_decrypted)
            return data_json
        except Exception as err:
            _LOGGER.error(
                "%s decode data error=%s",
                context,
                type(err).__name__,
            )
            raise

    async def _async_call_with_retry(self, func, *args):
        relogin_retried = False
        timeout_retries = 0
        while True:
            try:
                return await func(*args)
            except (EmptyDataException, ForbiddenException) as err:
                if relogin_retried:
                    # Reached max relogin retries
                    raise
                relogin_retried = True
                _LOGGER.debug(
                    "Re-login and retry potential expired auth error: %s",
                    err,
                )
            except TimeoutException as err:
                if timeout_retries >= self._timeout_error_retries:
                    # Reached max retries
                    raise
                timeout_retries += 1
                _LOGGER.debug(
                    "Retry (%d of %d) timeout error: %s",
                    timeout_retries,
                    self._timeout_error_retries,
                    err,
                )
