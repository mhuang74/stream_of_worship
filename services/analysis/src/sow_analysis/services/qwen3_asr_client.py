"""DashScope Qwen3 ASR client for LRC transcription."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

REGION_URLS = {
    "intl": "https://dashscope-intl.aliyuncs.com/api/v1",
    "cn": "https://dashscope.aliyuncs.com/api/v1",
    "us": "https://dashscope-us.aliyuncs.com/api/v1",
}
DIRECT_FLASH_MAX_SIZE_MB = 10.0
DIRECT_FLASH_MAX_DURATION_SECONDS = 300.0


class Qwen3AsrError(Exception):
    """Base error for Qwen3 ASR failures."""


class Qwen3AsrNonRetriableError(Qwen3AsrError):
    """Raised for auth/configuration errors that should not be retried."""


class Qwen3AsrTimeoutError(Qwen3AsrError):
    """Raised when DashScope does not complete before timeout."""


@dataclass
class Qwen3AsrSegment:
    text: str
    start: float
    end: float


@dataclass
class Qwen3AsrWord:
    text: str
    start: float
    end: float


@dataclass
class Qwen3AsrResult:
    segments: list[Qwen3AsrSegment]
    words: list[Qwen3AsrWord]
    text: str
    raw_response: dict[str, Any]
    model: str
    region: str
    mode: str

    def to_cache_payload(self) -> dict[str, Any]:
        return {
            "segments": [s.__dict__ for s in self.segments],
            "words": [w.__dict__ for w in self.words],
            "text": self.text,
            "raw_response": self.raw_response,
            "model": self.model,
            "region": self.region,
            "mode": self.mode,
        }

    @classmethod
    def from_cache_payload(cls, payload: dict[str, Any]) -> "Qwen3AsrResult":
        return cls(
            segments=[Qwen3AsrSegment(**s) for s in payload.get("segments", [])],
            words=[Qwen3AsrWord(**w) for w in payload.get("words", [])],
            text=str(payload.get("text") or ""),
            raw_response=dict(payload.get("raw_response") or {}),
            model=str(payload.get("model") or settings.SOW_DASHSCOPE_ASR_FLASH_MODEL),
            region=str(payload.get("region") or settings.SOW_DASHSCOPE_ASR_REGION),
            mode=str(payload.get("mode") or "cache"),
        )


class Qwen3AsrClient:
    """Async wrapper around the blocking DashScope SDK."""

    _circuit_open = False

    def __init__(
        self,
        api_key: str,
        region: str = "intl",
        flash_model: str = "qwen3-asr-flash",
        filetrans_model: str = "qwen3-asr-flash-filetrans",
    ):
        self.api_key = api_key
        self.region = region
        self.flash_model = flash_model
        self.filetrans_model = filetrans_model

    async def transcribe(self, audio_path: Path, context: str = "") -> Qwen3AsrResult:
        if not self.api_key:
            raise Qwen3AsrNonRetriableError("SOW_DASHSCOPE_API_KEY is not configured")
        if self._circuit_open:
            raise Qwen3AsrNonRetriableError("DashScope Qwen3 ASR circuit breaker is open")

        mode = self._choose_mode(audio_path)
        try:
            if mode == "direct":
                return await self._with_retries(
                    lambda: self._transcribe_direct(audio_path, context)
                )
            return await self._with_retries(lambda: self._transcribe_filetrans(audio_path))
        except Qwen3AsrNonRetriableError:
            self.__class__._circuit_open = True
            raise
        except Qwen3AsrError:
            if mode == "direct":
                logger.info("Qwen3 ASR direct call failed; trying filetrans fallback once")
                return await self._with_retries(lambda: self._transcribe_filetrans(audio_path))
            raise

    def _choose_mode(self, audio_path: Path) -> str:
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        if size_mb > DIRECT_FLASH_MAX_SIZE_MB:
            logger.info("Routing Qwen3 ASR to filetrans: audio size %.1fMB", size_mb)
            return "filetrans"

        duration_seconds = self._probe_duration_seconds(audio_path)
        if duration_seconds is not None and duration_seconds > DIRECT_FLASH_MAX_DURATION_SECONDS:
            logger.info(
                "Routing Qwen3 ASR to filetrans: audio duration %.1fs", duration_seconds
            )
            return "filetrans"

        logger.info(
            "Routing Qwen3 ASR to direct flash: audio size %.1fMB duration %s",
            size_mb,
            f"{duration_seconds:.1f}s" if duration_seconds is not None else "unknown",
        )
        return "direct"

    def _probe_duration_seconds(self, audio_path: Path) -> Optional[float]:
        try:
            import librosa

            return float(librosa.get_duration(path=audio_path))
        except Exception as exc:
            logger.warning("Could not probe audio duration for Qwen3 ASR routing: %s", exc)
            return None

    async def _with_retries(self, call):
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                return await call()
            except Qwen3AsrNonRetriableError:
                raise
            except Qwen3AsrTimeoutError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(2**attempt)
        raise Qwen3AsrError(f"Qwen3 ASR failed after retries: {last_error}") from last_error

    async def _transcribe_direct(self, audio_path: Path, context: str) -> Qwen3AsrResult:
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            import dashscope

            dashscope.base_http_api_url = REGION_URLS.get(self.region, REGION_URLS["intl"])
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": [{"audio": f"file://{audio_path.resolve()}"}]},
            ]
            if context:
                messages.insert(0, {"role": "system", "content": [{"text": context}]})
            resp = dashscope.MultiModalConversation.call(
                api_key=self.api_key,
                model=self.flash_model,
                messages=messages,
                result_format="message",
                asr_options={"enable_itn": False, "enable_words": True, "language": "zh"},
            )
            self._raise_for_response(resp)
            return dict(resp.output or {})

        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _call),
            timeout=settings.SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS,
        )
        return self._parse_result(raw, self.flash_model, "direct")

    async def _transcribe_filetrans(self, audio_path: Path) -> Qwen3AsrResult:
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            import dashscope
            from dashscope.audio.qwen_asr import QwenTranscription
            from dashscope.utils.oss_utils import OssUtils

            dashscope.base_http_api_url = REGION_URLS.get(self.region, REGION_URLS["intl"])
            file_url, _ = OssUtils.upload(
                model=self.filetrans_model,
                file_path=str(audio_path.resolve()),
                api_key=self.api_key,
            )
            if not file_url:
                raise Qwen3AsrError("DashScope OSS upload returned no file URL")
            task = QwenTranscription.async_call(
                model=self.filetrans_model,
                file_url=file_url,
                api_key=self.api_key,
                enable_words=True,
                headers={"X-DashScope-OssResourceResolve": "enable"},
            )
            self._raise_for_response(task)
            start = time.time()
            while time.time() - start < settings.SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS:
                resp = QwenTranscription.fetch(task=task, api_key=self.api_key)
                self._raise_for_response(resp)
                output = dict(resp.output or {})
                status = output.get("task_status")
                if status == "SUCCEEDED":
                    return output
                if status in {"FAILED", "CANCELED"}:
                    raise Qwen3AsrError(f"DashScope filetrans task failed: {output}")
                time.sleep(5)
            raise Qwen3AsrTimeoutError("DashScope filetrans polling timed out")

        raw = await loop.run_in_executor(None, _call)
        fetched = await self._fetch_filetrans_json(raw)
        return self._parse_result(fetched, self.filetrans_model, "filetrans")

    async def _fetch_filetrans_json(self, raw: dict[str, Any]) -> dict[str, Any]:
        url = raw.get("result", {}).get("transcription_url") or (raw.get("results") or [{}])[0].get(
            "transcription_url"
        )
        if not url:
            return raw
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        return {"filetrans_task": raw, **data}

    def _raise_for_response(self, resp: Any) -> None:
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return
        message = str(getattr(resp, "message", "") or getattr(resp, "output", ""))
        if status in {401, 403}:
            raise Qwen3AsrNonRetriableError(f"DashScope auth error {status}: {message}")
        if status == 429 or status >= 500:
            raise Qwen3AsrError(f"DashScope transient error {status}: {message}")
        raise Qwen3AsrError(f"DashScope API error {status}: {message}")

    def _parse_result(self, raw: dict[str, Any], model: str, mode: str) -> Qwen3AsrResult:
        sentences = self._extract_sentences(raw)
        words = self._extract_words(raw)
        segments = [
            Qwen3AsrSegment(
                text=str(s.get("text", "")).strip(),
                start=self._ms_to_seconds(
                    s.get("begin_time", s.get("start_time", s.get("start", 0)))
                ),
                end=self._ms_to_seconds(s.get("end_time", s.get("end", 0))),
            )
            for s in sentences
            if str(s.get("text", "")).strip()
        ]
        if not segments and words:
            segments = [
                Qwen3AsrSegment("".join(w.text for w in words), words[0].start, words[-1].end)
            ]
        text = "".join(s.text for s in segments).strip()
        if not text or not segments:
            raise Qwen3AsrError("Qwen3 ASR returned no usable transcription")
        return Qwen3AsrResult(segments, words, text, raw, model, self.region, mode)

    def _extract_sentences(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        direct = raw.get("choices", [{}])[0].get("message", {}).get("content", [])
        for item in direct:
            if item.get("type") == "audio_transcription":
                return item.get("audio_transcription_results", {}).get("sentences", [])
        if raw.get("sentences"):
            return raw["sentences"]
        if raw.get("transcripts"):
            sentences: list[dict[str, Any]] = []
            for transcript in raw["transcripts"]:
                transcript_sentences = (
                    transcript.get("sentences") if isinstance(transcript, dict) else None
                )
                if transcript_sentences:
                    sentences.extend(transcript_sentences)
            return sentences
        return raw.get("results", [])

    def _extract_words(self, raw: dict[str, Any]) -> list[Qwen3AsrWord]:
        candidates: list[dict[str, Any]] = []
        for sentence in self._extract_sentences(raw):
            candidates.extend(sentence.get("words") or [])
        candidates.extend(raw.get("words") or [])
        words = []
        for item in candidates:
            text = str(item.get("text") or item.get("word") or "").strip()
            if text:
                words.append(
                    Qwen3AsrWord(
                        text=text,
                        start=self._ms_to_seconds(item.get("begin_time", item.get("start", 0))),
                        end=self._ms_to_seconds(item.get("end_time", item.get("end", 0))),
                    )
                )
        return words

    def _ms_to_seconds(self, value: Any) -> float:
        return float(value or 0) / 1000.0
