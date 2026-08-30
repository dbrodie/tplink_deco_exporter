from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from .api import TplinkDecoApi
from .config import LogsConfig, LokiConfig
from .prometheus_metrics import DecoMetrics

_LOGGER = logging.getLogger(__name__)
_TIMESTAMP = re.compile(
    r"^(?:(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})|([A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \d{4}))"
)
_LEVELS = {
    "ALL": 8,
    "ALERT": 1,
    "CRITICAL": 2,
    "ERROR": 3,
    "WARNING": 4,
    "NOTICE": 5,
    "INFO": 6,
    "DEBUG": 7,
}


class LokiPushFailed(Exception):
    """A Loki batch was not accepted after configured retries."""


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


@dataclass
class Watermark:
    final_router_timestamp: str | None
    anchor_hashes: list[str]
    last_loki_push_time: float | None


class StateStore:
    def __init__(self, path: Path, identity: str, metrics: DecoMetrics):
        self.path, self.identity, self.metrics = path, identity, metrics
        self._nodes: dict[str, Watermark] | None = None
        self._legacy: Watermark | None = None

    def empty(self) -> Watermark:
        return Watermark(None, [], None)

    def _load_file(self, device_mac: str):
        if self._nodes is not None:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("identity") != self.identity:
                raise ValueError("state identity mismatch")
            if raw.get("version") == 2:
                node_data = raw.get("nodes")
                if not isinstance(node_data, dict):
                    raise TypeError("state nodes is not an object")
                self._nodes = {
                    str(mac): Watermark(**value)
                    for mac, value in node_data.items()
                    if isinstance(value, dict)
                }
                return
            if raw.get("version") == 1:
                self._legacy = Watermark(
                    raw.get("final_router_timestamp"),
                    list(raw.get("anchor_hashes") or []),
                    raw.get("last_loki_push_time"),
                )
                self._nodes = {}
                return
            raise ValueError("unsupported state version")
        except FileNotFoundError:
            self._nodes = {}
        except (OSError, ValueError, TypeError):
            self.metrics.watermark_resets.labels(device_mac, "invalid_state").inc()
            _LOGGER.warning("Ignoring invalid log watermark at %s", self.path)
            self._nodes = {}

    def load(self, device_mac: str, migrate_legacy: bool = False) -> Watermark:
        self._load_file(device_mac)
        assert self._nodes is not None
        if migrate_legacy and self._legacy is not None:
            state = self._legacy
            self._legacy = None
            self.save(device_mac, state)
            return state
        return self._nodes.get(device_mac, self.empty())

    def save(self, device_mac: str, state: Watermark):
        self._load_file(device_mac)
        assert self._nodes is not None
        self._nodes[device_mac] = state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "identity": self.identity,
                    "nodes": {
                        mac: asdict(node_state)
                        for mac, node_state in self._nodes.items()
                    },
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)


class LokiClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        config: LokiConfig,
        instance: str,
        metrics: DecoMetrics,
    ):
        self.session, self.config, self.instance, self.metrics = (
            session,
            config,
            instance,
            metrics,
        )

    async def push(self, records: list[tuple[int, str]], device_mac: str) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.config.tenant_id:
            headers["X-Scope-OrgID"] = self.config.tenant_id
        token = self.config.resolved_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        auth = None
        password = self.config.resolved_password()
        if self.config.username and password is not None:
            auth = aiohttp.BasicAuth(self.config.username, password)
        payload = {
            "streams": [
                {
                    "stream": {
                        "job": "tplink_deco",
                        "instance": self.instance,
                        "source": "deco_router",
                        "device_mac": device_mac,
                    },
                    "values": [[str(ns), line] for ns, line in records],
                }
            ]
        }
        for attempt in range(self.config.retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                async with self.session.post(
                    self.config.url,
                    json=payload,
                    headers=headers,
                    auth=auth,
                    ssl=self.config.verify_ssl,
                    timeout=timeout,
                ) as response:
                    if 200 <= response.status < 300:
                        self.metrics.loki_batches.labels(device_mac, "success").inc()
                        self.metrics.loki_records.labels(device_mac, "success").inc(
                            len(records)
                        )
                        return True
                    _LOGGER.warning(
                        "Loki rejected a batch with HTTP %s", response.status
                    )
            except (aiohttp.ClientError, TimeoutError) as exc:
                _LOGGER.warning(
                    "Loki push attempt %d failed: %s", attempt + 1, type(exc).__name__
                )
            if attempt < self.config.retries:
                import asyncio

                await asyncio.sleep(min(2**attempt, 10))
        self.metrics.loki_batches.labels(device_mac, "error").inc()
        self.metrics.loki_records.labels(device_mac, "error").inc(len(records))
        return False


class LogForwarder:
    def __init__(
        self,
        api: TplinkDecoApi,
        loki: LokiClient,
        config: LogsConfig,
        instance: str,
        metrics: DecoMetrics,
        device_mac: str,
        store: StateStore | None = None,
        migrate_legacy: bool = False,
    ):
        self.api, self.loki, self.config, self.metrics = api, loki, config, metrics
        self.device_mac = device_mac
        self.store = store or StateStore(
            config.state_path, f"{instance}:deco_router", metrics
        )
        self.migrate_legacy = migrate_legacy

    def _timestamp_ns(
        self, line: str, timezone_name: str
    ) -> tuple[int | None, str | None, str | None, str | None]:
        match = _TIMESTAMP.match(line)
        if not match:
            return (
                None,
                None,
                "missing_prefix",
                "leading timestamp did not match a supported firmware format",
            )
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo(self.config.timezone_fallback)
        raw = match.group(1) or match.group(2)
        try:
            is_iso = match.group(1) is not None
            pattern = "%Y-%m-%d %H:%M:%S" if is_iso else "%a %b %d %H:%M:%S %Y"
            normalized = raw.replace("T", " ", 1) if is_iso else raw
            parsed = datetime.strptime(normalized, pattern).replace(tzinfo=zone)
            return int(parsed.timestamp() * 1_000_000_000), raw, None, None
        except ValueError as exc:
            return None, None, "invalid_timestamp", str(exc)

    @staticmethod
    def _after_anchor(lines: list[str], anchor: list[str]) -> tuple[list[str], bool]:
        if not anchor:
            return lines, False
        hashes = [line_hash(line) for line in lines if line.strip()]
        for index in range(len(hashes) - len(anchor), -1, -1):
            if hashes[index : index + len(anchor)] == anchor:
                return [line for line in lines if line.strip()][
                    index + len(anchor) :
                ], True
        return lines, False

    async def poll(self, timezone_name: str):
        async def api_call(endpoint, call):
            started = time.monotonic()
            try:
                result = await call()
            except Exception:
                self.metrics.api_requests.labels(endpoint, "error").inc()
                raise
            else:
                self.metrics.api_requests.labels(endpoint, "success").inc()
                self.metrics.api_last_success.labels(endpoint).set(time.time())
                self.metrics.endpoint_supported.labels(endpoint).set(1)
                return result
            finally:
                self.metrics.api_duration.labels(endpoint).observe(
                    time.monotonic() - started
                )

        types = await api_call(
            "log_types",
            lambda: self.api.async_request(
                "log_export", "types", expected_result_type=list
            ),
        )
        advertised = {
            str(item.get("name", "")).upper()
            for item in types
            if isinstance(item, dict)
        }
        if advertised and self.config.level not in advertised:
            _LOGGER.warning(
                "Configured firmware log level %s was not advertised by device_mac=%s",
                self.config.level,
                self.device_mac,
            )
        await api_call(
            "feedback_log", lambda: self.api.async_build_log(_LEVELS[self.config.level])
        )
        newest = await api_call(
            "feedback_log", lambda: self.api.async_log_page(0, self.config.page_size)
        )
        page_count = int(newest.get("totalNum", 0))
        self.metrics.log_pages.labels(self.device_mac).set(page_count)
        pages: dict[int, list[str]] = {
            int(newest.get("currentIndex", 0)): [
                str(x.get("content", "")) for x in newest.get("logList", [])
            ]
        }
        state = self.store.load(self.device_mac, self.migrate_legacy)
        # Firmware page zero is newest. Fetch backward in time until the anchor can be found.
        for index in range(1, page_count):
            page = await api_call(
                "feedback_log",
                lambda i=index: self.api.async_log_page(i, self.config.page_size),
            )
            pages[int(page.get("currentIndex", index))] = [
                str(x.get("content", "")) for x in page.get("logList", [])
            ]
            chronological = [
                line
                for page_index in sorted(pages, reverse=True)
                for line in pages[page_index]
            ]
            if (
                state.anchor_hashes
                and self._after_anchor(chronological, state.anchor_hashes)[1]
            ):
                break
        raw_lines = [
            line
            for page_index in sorted(pages, reverse=True)
            for line in pages[page_index]
        ]
        self.metrics.log_records.labels(self.device_mac).inc(len(raw_lines))
        lines = [line for line in raw_lines if line.strip()]
        blank_records = len(raw_lines) - len(lines)
        if blank_records:
            self.metrics.log_blank_records.labels(self.device_mac).inc(blank_records)
            _LOGGER.debug(
                "Ignoring blank firmware log records device_mac=%s count=%s",
                self.device_mac,
                blank_records,
            )
        pending, found = self._after_anchor(lines, state.anchor_hashes)
        if state.anchor_hashes and not found:
            self.metrics.watermark_resets.labels(
                self.device_mac, "anchor_missing"
            ).inc()
        rolling = [line for line in lines if line.strip()][:0]
        if found:
            anchor_start = max(
                0,
                len([x for x in lines if x.strip()])
                - len(pending)
                - len(state.anchor_hashes),
            )
            rolling = [x for x in lines if x.strip()][
                anchor_start : anchor_start + len(state.anchor_hashes)
            ]
        last_ns = 0
        last_router_timestamp = state.final_router_timestamp
        for offset in range(0, len(pending), self.loki.config.batch_size):
            batch = pending[offset : offset + self.loki.config.batch_size]
            values = []
            delivered_lines = []
            for line in batch:
                ns, router_timestamp, parse_error, error_detail = self._timestamp_ns(
                    line, timezone_name
                )
                if parse_error and error_detail:
                    self.metrics.log_timestamp_parse_errors.labels(
                        self.device_mac, parse_error
                    ).inc()
                    _LOGGER.error(
                        "Dropping firmware log record with unparseable timestamp "
                        "device_mac=%s reason=%s error=%s line=%r",
                        self.device_mac,
                        parse_error,
                        error_detail,
                        line,
                    )
                    continue
                assert ns is not None
                last_ns = max(ns, last_ns + 1)
                values.append((last_ns, line))
                delivered_lines.append(line)
                if router_timestamp:
                    last_router_timestamp = router_timestamp
            if not values:
                continue
            if not await self.loki.push(values, self.device_mac):
                raise LokiPushFailed
            rolling.extend(delivered_lines)
            rolling = rolling[-8:]
            state = Watermark(
                last_router_timestamp,
                [line_hash(x) for x in rolling],
                time.time(),
            )
            self.store.save(self.device_mac, state)


class MeshLogForwarder:
    def __init__(
        self,
        root_api: TplinkDecoApi,
        api_factory: Callable[[str], TplinkDecoApi],
        root_ip: str,
        loki: LokiClient,
        config: LogsConfig,
        instance: str,
        metrics: DecoMetrics,
    ):
        self.root_api = root_api
        self.api_factory = api_factory
        self.root_ip = root_ip
        self.loki = loki
        self.config = config
        self.instance = instance
        self.metrics = metrics
        self.store = StateStore(config.state_path, f"{instance}:deco_router", metrics)
        self.forwarders: dict[str, tuple[str, LogForwarder]] = {}

    def _forwarder(self, device_mac: str, device_ip: str) -> LogForwarder:
        current = self.forwarders.get(device_mac)
        if current is not None and current[0] == device_ip:
            return current[1]
        api = (
            self.root_api if device_ip == self.root_ip else self.api_factory(device_ip)
        )
        forwarder = LogForwarder(
            api,
            self.loki,
            self.config,
            self.instance,
            self.metrics,
            device_mac,
            self.store,
            migrate_legacy=device_ip == self.root_ip,
        )
        self.forwarders[device_mac] = (device_ip, forwarder)
        return forwarder

    async def poll(
        self, devices: list[dict], timezone_name: str
    ) -> tuple[int, list[tuple[str, Exception]]]:
        candidates = [
            (str(device.get("mac", "")), str(device.get("device_ip", "")))
            for device in devices
            if device.get("mac") and device.get("device_ip")
        ]
        candidates.sort(key=lambda item: item[1] != self.root_ip)
        errors: list[tuple[str, Exception]] = []
        for device_mac, device_ip in candidates:
            try:
                await self._forwarder(device_mac, device_ip).poll(timezone_name)
            except Exception as exc:  # noqa: BLE001 - isolate each Deco log source
                self.metrics.log_node_errors.labels(
                    device_mac, type(exc).__name__
                ).inc()
                errors.append((device_mac, exc))
            else:
                self.metrics.log_node_last_success.labels(device_mac).set(time.time())
        return len(candidates), errors
