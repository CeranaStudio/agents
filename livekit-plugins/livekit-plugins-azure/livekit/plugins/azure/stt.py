# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import contextlib
import os
import weakref
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

import azure.cognitiveservices.speech as speechsdk  # type: ignore
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    stt,
    utils,
)
from livekit.agents.types import (
    NOT_GIVEN,
    NotGivenOr,
)
from livekit.agents.utils import is_given

from .log import logger


@dataclass
class STTOptions:
    speech_key: NotGivenOr[str]
    speech_region: NotGivenOr[str]
    # see https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-container-stt?tabs=container#use-the-container
    speech_host: NotGivenOr[str]
    # for using Microsoft Entra auth (see https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ad-auth?tabs=portal&pivots=programming-language-python)
    speech_auth_token: NotGivenOr[str]
    sample_rate: int
    num_channels: int
    segmentation_silence_timeout_ms: NotGivenOr[int]
    segmentation_max_time_ms: NotGivenOr[int]
    segmentation_strategy: NotGivenOr[str]
    language: list[
        str
    ]  # see https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=stt
    speech_endpoint: NotGivenOr[str] = NOT_GIVEN
    profanity: NotGivenOr[speechsdk.enums.ProfanityOption] = NOT_GIVEN
    phrase_list: NotGivenOr[list[str] | None] = NOT_GIVEN


class STT(stt.STT):
    def __init__(
        self,
        *,
        speech_key: NotGivenOr[str] = NOT_GIVEN,
        speech_region: NotGivenOr[str] = NOT_GIVEN,
        speech_host: NotGivenOr[str] = NOT_GIVEN,
        speech_auth_token: NotGivenOr[str] = NOT_GIVEN,
        sample_rate: int = 16000,
        num_channels: int = 1,
        segmentation_silence_timeout_ms: NotGivenOr[int] = NOT_GIVEN,
        segmentation_max_time_ms: NotGivenOr[int] = NOT_GIVEN,
        segmentation_strategy: NotGivenOr[str] = NOT_GIVEN,
        # Azure handles multiple languages and can auto-detect the language used. It requires the candidate set to be set.  # noqa: E501
        language: NotGivenOr[str | list[str] | None] = NOT_GIVEN,
        profanity: NotGivenOr[speechsdk.enums.ProfanityOption] = NOT_GIVEN,
        speech_endpoint: NotGivenOr[str] = NOT_GIVEN,
        phrase_list: NotGivenOr[list[str] | None] = NOT_GIVEN,
    ):
        """
        Create a new instance of Azure STT.

        Either ``speech_host`` or ``speech_key`` and ``speech_region`` or
        ``speech_auth_token`` and ``speech_region`` or
        ``speech_key`` and ``speech_endpoint``
        must be set using arguments.
         Alternatively,  set the ``AZURE_SPEECH_HOST``, ``AZURE_SPEECH_KEY``
        and ``AZURE_SPEECH_REGION`` environmental variables, respectively.
        ``speech_auth_token`` must be set using the arguments as it's an ephemeral token.

        Args:
            phrase_list: List of words or phrases to boost recognition accuracy.
                        Azure will give higher priority to these phrases during recognition.
        """

        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        if not language or not is_given(language):
            language = ["en-US"]

        if isinstance(language, str):
            language = [language]

        if not is_given(speech_host):
            speech_host = os.environ.get("AZURE_SPEECH_HOST") or NOT_GIVEN

        if not is_given(speech_key):
            speech_key = os.environ.get("AZURE_SPEECH_KEY") or NOT_GIVEN

        if not is_given(speech_region):
            speech_region = os.environ.get("AZURE_SPEECH_REGION") or NOT_GIVEN

        if not (
            is_given(speech_host)
            or (is_given(speech_key) and is_given(speech_region))
            or (is_given(speech_auth_token) and is_given(speech_region))
            or (is_given(speech_key) and is_given(speech_endpoint))
        ):
            raise ValueError(
                "AZURE_SPEECH_HOST or AZURE_SPEECH_KEY and AZURE_SPEECH_REGION or speech_auth_token and AZURE_SPEECH_REGION or AZURE_SPEECH_KEY and speech_endpoint must be set"  # noqa: E501
            )

        if speech_region and speech_endpoint:
            logger.warning("speech_region and speech_endpoint both are set, using speech_endpoint")
            speech_region = NOT_GIVEN

        self._config = STTOptions(
            speech_key=speech_key,
            speech_region=speech_region,
            speech_host=speech_host,
            speech_auth_token=speech_auth_token,
            language=language,
            sample_rate=sample_rate,
            num_channels=num_channels,
            segmentation_silence_timeout_ms=segmentation_silence_timeout_ms,
            segmentation_max_time_ms=segmentation_max_time_ms,
            segmentation_strategy=segmentation_strategy,
            profanity=profanity,
            speech_endpoint=speech_endpoint,
            phrase_list=phrase_list,
        )
        self._streams = weakref.WeakSet[SpeechStream]()

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("Azure STT does not support single frame recognition")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        config = deepcopy(self._config)
        if is_given(language):
            config.language = [language]
        stream = SpeechStream(stt=self, opts=config, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def update_options(self, *, language: NotGivenOr[list[str] | str] = NOT_GIVEN) -> None:
        if is_given(language):
            if isinstance(language, str):
                language = [language]
            language = cast(list[str], language)
            self._config.language = language
            for stream in self._streams:
                stream.update_options(language=language)


class SpeechStream(stt.SpeechStream):
    def __init__(self, *, stt: STT, opts: STTOptions, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts
        self._speaking = False

        self._session_stopped_event = asyncio.Event()
        self._session_started_event = asyncio.Event()

        self._loop = asyncio.get_running_loop()
        self._reconnect_event = asyncio.Event()

    def update_options(self, *, language: list[str]) -> None:
        self._opts.language = language
        self._reconnect_event.set()

    async def _run(self) -> None:
        while True:
            self._session_stopped_event.clear()

            self._stream = speechsdk.audio.PushAudioInputStream(
                stream_format=speechsdk.audio.AudioStreamFormat(
                    samples_per_second=self._opts.sample_rate,
                    bits_per_sample=16,
                    channels=self._opts.num_channels,
                )
            )
            self._recognizer = _create_speech_recognizer(config=self._opts, stream=self._stream)
            self._recognizer.recognizing.connect(self._on_recognizing)
            self._recognizer.recognized.connect(self._on_recognized)
            self._recognizer.speech_start_detected.connect(self._on_speech_start)
            self._recognizer.speech_end_detected.connect(self._on_speech_end)
            self._recognizer.session_started.connect(self._on_session_started)
            self._recognizer.session_stopped.connect(self._on_session_stopped)
            self._recognizer.canceled.connect(self._on_canceled)
            self._recognizer.start_continuous_recognition()

            try:
                await asyncio.wait_for(
                    self._session_started_event.wait(), self._conn_options.timeout
                )

                async def process_input() -> None:
                    async for input in self._input_ch:
                        if isinstance(input, rtc.AudioFrame):
                            self._stream.write(input.data.tobytes())

                process_input_task = asyncio.create_task(process_input())
                wait_reconnect_task = asyncio.create_task(self._reconnect_event.wait())
                wait_stopped_task = asyncio.create_task(self._session_stopped_event.wait())

                try:
                    done, _ = await asyncio.wait(
                        [process_input_task, wait_reconnect_task, wait_stopped_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        if task not in [wait_reconnect_task, wait_stopped_task]:
                            task.result()

                    if wait_stopped_task in done:
                        raise APIConnectionError("SpeechRecognition session stopped")

                    if wait_reconnect_task not in done:
                        break
                    self._reconnect_event.clear()
                finally:
                    await utils.aio.gracefully_cancel(process_input_task, wait_reconnect_task)

                self._stream.close()
                await self._session_stopped_event.wait()
            finally:

                def _cleanup() -> None:
                    self._recognizer.stop_continuous_recognition()
                    del self._recognizer

                await asyncio.to_thread(_cleanup)

    def _on_recognized(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        detected_lg = speechsdk.AutoDetectSourceLanguageResult(evt.result).language
        text = evt.result.text.strip()
        if not text:
            return

        if not detected_lg and self._opts.language:
            detected_lg = self._opts.language[0]

        final_data = stt.SpeechData(language=detected_lg, confidence=1.0, text=evt.result.text)

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(
                self._event_ch.send_nowait,
                stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[final_data]
                ),
            )

    def _on_recognizing(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        detected_lg = speechsdk.AutoDetectSourceLanguageResult(evt.result).language
        text = evt.result.text.strip()
        if not text:
            return

        if not detected_lg and self._opts.language:
            detected_lg = self._opts.language[0]

        interim_data = stt.SpeechData(language=detected_lg, confidence=0.0, text=evt.result.text)

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(
                self._event_ch.send_nowait,
                stt.SpeechEvent(
                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    alternatives=[interim_data],
                ),
            )

    def _on_speech_start(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if self._speaking:
            return

        self._speaking = True

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(
                self._event_ch.send_nowait,
                stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH),
            )

    def _on_speech_end(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if not self._speaking:
            return

        self._speaking = False

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(
                self._event_ch.send_nowait,
                stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH),
            )

    def _on_session_started(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        self._session_started_event.set()

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._session_started_event.set)

    def _on_session_stopped(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._session_stopped_event.set)

    def _on_canceled(self, evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            logger.warning(
                f"Speech recognition canceled: {evt.cancellation_details}",
                extra={
                    "code": evt.cancellation_details.code,
                    "reason": evt.cancellation_details.reason,
                    "error_details": evt.cancellation_details.error_details,
                },
            )


def _create_speech_recognizer(
    *, config: STTOptions, stream: speechsdk.audio.AudioInputStream
) -> speechsdk.SpeechRecognizer:
    # let the SpeechConfig constructor to validate the arguments
    speech_config = speechsdk.SpeechConfig(
        subscription=config.speech_key if is_given(config.speech_key) else None,
        region=config.speech_region if is_given(config.speech_region) else None,
        endpoint=config.speech_endpoint if is_given(config.speech_endpoint) else None,
        host=config.speech_host if is_given(config.speech_host) else None,
        auth_token=config.speech_auth_token if is_given(config.speech_auth_token) else None,
    )

    if config.segmentation_silence_timeout_ms:
        speech_config.set_property(
            speechsdk.enums.PropertyId.Speech_SegmentationSilenceTimeoutMs,
            str(config.segmentation_silence_timeout_ms),
        )
    if config.segmentation_max_time_ms:
        speech_config.set_property(
            speechsdk.enums.PropertyId.Speech_SegmentationMaximumTimeMs,
            str(config.segmentation_max_time_ms),
        )
    if config.segmentation_strategy:
        speech_config.set_property(
            speechsdk.enums.PropertyId.Speech_SegmentationStrategy,
            str(config.segmentation_strategy),
        )
    if is_given(config.profanity):
        speech_config.set_profanity(config.profanity)

    kwargs: dict[str, Any] = {}
    if config.language and len(config.language) > 1:
        kwargs["auto_detect_source_language_config"] = (
            speechsdk.languageconfig.AutoDetectSourceLanguageConfig(languages=config.language)
        )
    elif config.language and len(config.language) == 1:
        kwargs["language"] = config.language[0]

    audio_config = speechsdk.audio.AudioConfig(stream=stream)
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config, **kwargs
    )

    # Add phrase list for keyword boosting if provided
    if is_given(config.phrase_list) and isinstance(config.phrase_list, list) and config.phrase_list:
        phrase_list_grammar = speechsdk.PhraseListGrammar.from_recognizer(speech_recognizer)
        for phrase in config.phrase_list:
            phrase_list_grammar.addPhrase(phrase)

    return speech_recognizer
