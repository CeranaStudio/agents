from __future__ import annotations

import asyncio
import contextlib

import pytest

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, APIError, utils
from livekit.agents.tts import TTS, AvailabilityChangedEvent, FallbackAdapter
from livekit.agents.tts.tts import SynthesizedAudio, SynthesizeStream
from livekit.agents.utils.aio.channel import ChanEmpty

from .fake_tts import FakeTTS


class FallbackAdapterTester(FallbackAdapter):
    def __init__(
        self,
        tts: list[TTS],
        *,
        max_retry_per_tts: int = 1,  # only retry once by default
        sample_rate: int | None = None,
    ) -> None:
        super().__init__(
            tts,
            max_retry_per_tts=max_retry_per_tts,
            sample_rate=sample_rate,
        )

        self.on("tts_availability_changed", self._on_tts_availability_changed)

        self._availability_changed_ch: dict[int, utils.aio.Chan[AvailabilityChangedEvent]] = {
            id(t): utils.aio.Chan[AvailabilityChangedEvent]() for t in tts
        }

    def _on_tts_availability_changed(self, ev: AvailabilityChangedEvent) -> None:
        self._availability_changed_ch[id(ev.tts)].send_nowait(ev)

    def availability_changed_ch(
        self,
        tts: TTS,
    ) -> utils.aio.ChanReceiver[AvailabilityChangedEvent]:
        return self._availability_changed_ch[id(tts)]


async def test_tts_fallback() -> None:
    fake1 = FakeTTS(fake_exception=APIConnectionError("fake1 failed"))
    fake2 = FakeTTS(fake_audio_duration=5.0, sample_rate=48000)

    fallback_adapter = FallbackAdapterTester([fake1, fake2])

    async with fallback_adapter.synthesize("hello test") as stream:
        frames = []
        async for data in stream:
            frames.append(data.frame)

        assert fake1.synthesize_ch.recv_nowait()
        assert fake2.synthesize_ch.recv_nowait()

        assert rtc.combine_audio_frames(frames).duration == 5.01

    assert not fallback_adapter.availability_changed_ch(fake1).recv_nowait().available

    fake2.update_options(fake_audio_duration=0.0)

    with pytest.raises(APIConnectionError):
        async with fallback_adapter.synthesize("hello test") as stream:
            async for _ in stream:
                pass

    assert not fallback_adapter.availability_changed_ch(fake2).recv_nowait().available

    await fallback_adapter.aclose()


async def test_no_audio() -> None:
    fake1 = FakeTTS(fake_audio_duration=0.0)

    fallback_adapter = FallbackAdapterTester([fake1])

    with pytest.raises(APIConnectionError):
        async with fallback_adapter.synthesize("hello test chunked") as stream:
            async for _ in stream:
                pass

    # stream
    fake1.update_options(fake_audio_duration=5.0)

    async def _input_task(stream: SynthesizeStream, text: str):
        with contextlib.suppress(RuntimeError):
            stream.push_text(text)
            stream.flush()
            stream.end_input()

    async def _stream_task(text: str, audio_duration: float) -> None:
        fake1.update_options(fake_timeout=0.5, fake_audio_duration=audio_duration)

        async with fallback_adapter.stream() as stream:
            input_task = asyncio.create_task(_input_task(stream, text))

            segments = set()
            try:
                async for ev in stream:
                    segments.add(ev.segment_id)
            finally:
                await input_task

            if audio_duration > 0.0:
                assert len(segments) == 1
            else:
                assert len(segments) == 0

    await _stream_task("hello test normal", 1.0)

    with pytest.raises(APIError):
        await _stream_task("hello test no audio", 0.0)

    await fallback_adapter.aclose()


async def test_tts_stream_fallback() -> None:
    fake1 = FakeTTS(fake_exception=APIConnectionError("fake1 failed"))
    fake2 = FakeTTS(fake_audio_duration=5.0)

    fallback_adapter = FallbackAdapterTester([fake1, fake2])

    async with fallback_adapter.stream() as stream:
        stream.push_text("hello test")
        stream.end_input()

        async for _ in stream:
            pass

        assert fake1.stream_ch.recv_nowait()
        assert fake2.stream_ch.recv_nowait()

    assert not fallback_adapter.availability_changed_ch(fake1).recv_nowait().available

    await fallback_adapter.aclose()


async def test_tts_recover() -> None:
    fake1 = FakeTTS(fake_exception=APIConnectionError("fake1 failed"))
    fake2 = FakeTTS(fake_exception=APIConnectionError("fake2 failed"), fake_timeout=0.5)

    fallback_adapter = FallbackAdapterTester([fake1, fake2])

    with pytest.raises(APIConnectionError):
        async with fallback_adapter.synthesize("hello test") as stream:
            async for _ in stream:
                pass

        assert fake1.synthesize_ch.recv_nowait()
        assert fake2.synthesize_ch.recv_nowait()

    fake2.update_options(fake_exception=None, fake_audio_duration=5.0)

    assert not fallback_adapter.availability_changed_ch(fake1).recv_nowait().available
    assert not fallback_adapter.availability_changed_ch(fake2).recv_nowait().available

    assert (
        await asyncio.wait_for(fallback_adapter.availability_changed_ch(fake2).recv(), 1.0)
    ).available, "fake2 should have recovered"

    async with fallback_adapter.synthesize("hello test") as stream:
        async for _ in stream:
            pass

    assert fake1.synthesize_ch.recv_nowait()
    assert fake2.synthesize_ch.recv_nowait()

    with pytest.raises(ChanEmpty):
        fallback_adapter.availability_changed_ch(fake1).recv_nowait()

    with pytest.raises(ChanEmpty):
        fallback_adapter.availability_changed_ch(fake2).recv_nowait()

    await fallback_adapter.aclose()


async def test_audio_resampled() -> None:
    fake1 = FakeTTS(sample_rate=48000, fake_exception=APIConnectionError("fake1 failed"))
    fake2 = FakeTTS(fake_audio_duration=5.0, sample_rate=16000)

    fallback_adapter = FallbackAdapterTester([fake1, fake2])

    async with fallback_adapter.synthesize("hello test") as stream:
        frames: list[SynthesizedAudio] = []
        async for data in stream:
            frames.append(data)

        assert fake1.synthesize_ch.recv_nowait()
        assert fake2.synthesize_ch.recv_nowait()

        assert not fallback_adapter.availability_changed_ch(fake1).recv_nowait().available

        assert frames[-1].is_final is True, "last frame should be final"

        combined_frame = rtc.combine_audio_frames([f.frame for f in frames])
        assert combined_frame.duration == 5.01
        assert combined_frame.sample_rate == 48000

    assert await asyncio.wait_for(fake1.synthesize_ch.recv(), 1.0)

    async with fallback_adapter.stream() as stream:
        stream.push_text("hello test")
        stream.end_input()

        frames: list[SynthesizedAudio] = []
        async for data in stream:
            frames.append(data)

        assert fake2.stream_ch.recv_nowait()

        assert frames[-1].is_final is True, "last frame should be final"

        combined_frame = rtc.combine_audio_frames([f.frame for f in frames])
        assert combined_frame.duration == 5.01  # 5.0 + 0.01 final flag
        assert combined_frame.sample_rate == 48000

    await fallback_adapter.aclose()


async def test_timeout():
    fake1 = FakeTTS(fake_timeout=0.5, sample_rate=48000)
    fake2 = FakeTTS(fake_timeout=0.5, sample_rate=48000)

    fallback_adapter = FallbackAdapterTester([fake1, fake2])

    with pytest.raises(APIConnectionError):
        async with fallback_adapter.synthesize(
            "hello test",
            conn_options=APIConnectOptions(timeout=0.1, max_retry=0),
        ) as stream:
            async for _ in stream:
                pass

    assert fake1.synthesize_ch.recv_nowait()
    assert fake2.synthesize_ch.recv_nowait()

    assert not fallback_adapter.availability_changed_ch(fake1).recv_nowait().available
    assert not fallback_adapter.availability_changed_ch(fake2).recv_nowait().available

    assert await asyncio.wait_for(fake1.synthesize_ch.recv(), 1.0)
    assert await asyncio.wait_for(fake2.synthesize_ch.recv(), 1.0)

    # stream
    with pytest.raises(APIConnectionError):
        async with fallback_adapter.stream(
            conn_options=APIConnectOptions(timeout=0.1, max_retry=0)
        ) as stream:
            stream.push_text("hello test")
            stream.end_input()

            async for _ in stream:
                pass

    assert fake1.stream_ch.recv_nowait()
    assert fake2.stream_ch.recv_nowait()

    assert await asyncio.wait_for(fake1.stream_ch.recv(), 1.0)
    assert await asyncio.wait_for(fake2.stream_ch.recv(), 1.0)

    await fallback_adapter.aclose()
