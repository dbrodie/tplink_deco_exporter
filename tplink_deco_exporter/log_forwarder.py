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


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


@dataclass
class Watermark:
    version: int
    identity: str
    final_router_timestamp: str | None
    anchor_hashes: list[str]
    last_loki_push_time: float | None


class StateStore:
    def __init__(self, path: Path, identity: str, metrics: DecoMetrics):
        self.path, self.identity, self.metrics = path, identity, metrics

    def empty(self) -> Watermark:
        return Watermark(1, self.identity, None, [], None)

    def load(self) -> Watermark:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = Watermark(**raw)
            if state.version != 1 or state.identity != self.identity:
                self.metrics.watermark_resets.labels("identity_or_version").inc()
                return self.empty()
            return state
        except FileNotFoundError:
            return self.empty()
        except (OSError, ValueError, TypeError):
            self.metrics.watermark_resets.labels("invalid_state").inc()
            _LOGGER.warning("Ignoring invalid log watermark at %s", self.path)
            return self.empty()

    def save(self, state: Watermark):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(asdict(state), separators=(",", ":")), encoding="utf-8"
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

    async def push(self, records: list[tuple[int, str]]) -> bool:
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
                        self.metrics.loki_batches.labels("success").inc()
                        self.metrics.loki_records.labels("success").inc(len(records))
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
        self.metrics.loki_batches.labels("error").inc()
        self.metrics.loki_records.labels("error").inc(len(records))
        return False


class LogForwarder:
    def __init__(
        self,
        api: TplinkDecoApi,
        loki: LokiClient,
        config: LogsConfig,
        instance: str,
        metrics: DecoMetrics,
    ):
        self.api, self.loki, self.config, self.metrics = api, loki, config, metrics
        self.store = StateStore(config.state_path, f"{instance}:deco_router", metrics)

    def _timestamp_ns(
        self, line: str, fallback_ns: int, timezone_name: str
    ) -> tuple[int, str | None]:
        match = _TIMESTAMP.match(line)
        if not match:
            return fallback_ns, None
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo(self.config.timezone_fallback)
        raw = match.group(1) or match.group(2)
        try:
            pattern = "%Y-%m-%d %H:%M:%S" if match.group(1) else "%a %b %d %H:%M:%S %Y"
            parsed = datetime.strptime(raw.replace("T", " "), pattern).replace(
                tzinfo=zone
            )
            return int(parsed.timestamp() * 1_000_000_000), raw
        except ValueError:
            return fallback_ns, None

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
            "log_types", lambda: self.api.async_request("log_export", "types")
        )
        advertised = {
            str(item.get("name", "")).upper()
            for item in types.get("types", types.get("type_list", []))
        }
        if advertised and self.config.level not in advertised:
            _LOGGER.warning(
                "Configured firmware log level %s was not advertised", self.config.level
            )
        await api_call(
            "feedback_log", lambda: self.api.async_build_log(_LEVELS[self.config.level])
        )
        newest = await api_call(
            "feedback_log", lambda: self.api.async_log_page(0, self.config.page_size)
        )
        page_count = int(newest.get("totalNum", 0))
        self.metrics.log_pages.set(page_count)
        pages: dict[int, list[str]] = {
            int(newest.get("currentIndex", 0)): [
                str(x.get("content", "")) for x in newest.get("logList", [])
            ]
        }
        state = self.store.load()
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
        lines = [
            line
            for page_index in sorted(pages, reverse=True)
            for line in pages[page_index]
        ]
        self.metrics.log_records.inc(len(lines))
        pending, found = self._after_anchor(lines, state.anchor_hashes)
        if state.anchor_hashes and not found:
            self.metrics.watermark_resets.labels("anchor_missing").inc()
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
        fallback = time.time_ns()
        last_ns = 0
        last_router_timestamp = state.final_router_timestamp
        for offset in range(0, len(pending), self.loki.config.batch_size):
            batch = pending[offset : offset + self.loki.config.batch_size]
            values = []
            for sequence, line in enumerate(batch):
                ns, router_timestamp = self._timestamp_ns(
                    line, fallback + offset + sequence, timezone_name
                )
                last_ns = max(ns, last_ns + 1)
                values.append((last_ns, line))
                if router_timestamp:
                    last_router_timestamp = router_timestamp
            if not await self.loki.push(values):
                return
            rolling.extend(line for line in batch if line.strip())
            rolling = rolling[-8:]
            state = Watermark(
                1,
                self.store.identity,
                last_router_timestamp,
                [line_hash(x) for x in rolling],
                time.time(),
            )
            self.store.save(state)
