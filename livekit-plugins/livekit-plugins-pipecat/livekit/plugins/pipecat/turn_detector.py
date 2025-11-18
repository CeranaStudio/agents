# Copyright 2024 LiveKit, Inc.
#
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

import time

from livekit import rtc
from livekit.agents import llm
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.metrics.metrics import SmartTurnMetricsData

from .log import logger


class PipecatSmartTurnDetector:
    """Turn Detector using Pipecat's Smart Turn model for end-of-turn detection based on audio analysis.

    This turn detector uses Pipecat's Smart Turn model to detect if the user has finished speaking.
    It needs to receive audio frames for analysis, rather than relying on text.

    Note: This class implements the _TurnDetector Protocol, and does not need to explicitly inherit it.
    As long as it implements all the methods required by the Protocol, it automatically conforms to the Protocol.
    """
    def __init__(
        self,
        *,
        model_path: str | None = None,
        sample_rate: int = 16000,
        unlikely_threshold: float = 0.5,
    ):
        """
        Args:
            model_path: Path to the Smart Turn model (optional, will use default model if not provided)
            sample_rate: Audio sample rate, default 16000 Hz
            unlikely_threshold: Probability threshold for end-of-turn detection, below which is considered unlikely to end
        """
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._unlikely_threshold = unlikely_threshold

        # initialize analyzer with sample_rate
        # Note: BaseSmartTurn needs sample_rate parameter
        self._analyzer = LocalSmartTurnAnalyzerV3(
            sample_rate=sample_rate,
            smart_turn_model_path=model_path,
        )

    @property
    def model(self) -> str:
        return "pipecat-smart-turn-v3"

    @property
    def provider(self) -> str:
        return "pipecat"

    @property
    def is_audio_turn_detector(self) -> bool:
        return True

    def append_audio(self, frame: rtc.AudioFrame) -> None:
        """Receive audio frame and pass it to Smart Turn analyzer

        This method should be called by AudioRecognition when processing audio.
        We convert the audio frame to bytes and pass it to BaseSmartTurn.append_audio.

        Note: Since we cannot directly get the speech status (is_speech) from the frame,
        we assume all incoming audio is speech, because this method is usually only called when the user is speaking.
        """
        if self._analyzer is None:
            return

        # Convert AudioFrame to bytes (16-bit PCM)
        audio_bytes = frame.data.tobytes()

        # Call BaseSmartTurn.append_audio
        # is_speech=True because this method is usually only called when the user is speaking
        # If the analyzer has VAD logic, it will handle it itself
        self._analyzer.append_audio(audio_bytes, is_speech=True)

    def clear_buffer(self) -> None:
        """Clear audio buffer"""
        if self._analyzer is not None:
            self._analyzer.clear()

    async def unlikely_threshold(self, language: str | None) -> float | None:
        """Return probability threshold for end-of-turn detection"""
        return self._unlikely_threshold

    async def supports_language(self, language: str | None) -> bool:
        """Whether the language is supported (Smart Turn usually supports multiple languages)"""
        return True

    async def predict_end_of_turn(
        self, chat_ctx: llm.ChatContext, *, timeout: float | None = None
    ) -> float:
        """Predict probability of end-of-turn

        Note: Although this method receives chat_ctx, we actually use accumulated audio data
        to predict. chat_ctx is ignored here because Smart Turn is based on audio.

        This method uses BaseSmartTurn.analyze_end_of_turn() to get the prediction result.
        The method will use the accumulated audio data through append_audio() for analysis.

        Args:
            chat_ctx: Chat context (ignored in this implementation)
            timeout: Timeout time (currently not used, but retained to conform to Protocol)

        Returns:
            Probability of end-of-turn (0.0 - 1.0), value越高表示越可能结束
        """
        if self._analyzer is None:
            # analyzer not initialized, return low probability
            return 0.0

        logger.debug("Starting Smart Turn prediction")

        try:
            # Use BaseSmartTurn.analyze_end_of_turn() to analyze audio
            # This method will use the accumulated audio data through append_audio() for analysis.

            time_start = time.perf_counter()
            state, metrics_data = await self._analyzer.analyze_end_of_turn()
            time_end = time.perf_counter()
            time_duration = time_end - time_start
            if state == EndOfTurnState.COMPLETE:
                logger.debug(f"\033[92mSmart Turn prediction result: COMPLETE\033[0m (duration: {time_duration:.2f}s)")
            elif state == EndOfTurnState.INCOMPLETE:
                logger.debug(f"\033[93mSmart Turn prediction result: INCOMPLETE\033[0m (duration: {time_duration:.2f}s)")
            else:
                logger.warning(f"Smart Turn prediction result: UNKNOWN (duration: {time_duration:.2f}s)")

            # Extract probability from metrics_data
            if metrics_data is not None and isinstance(metrics_data, SmartTurnMetricsData):
                # SmartTurnMetricsData has probability attribute
                probability = metrics_data.probability
            elif state == EndOfTurnState.COMPLETE:
                # If state is COMPLETE, return high probability
                probability = 0.9
            else:
                # If state is INCOMPLETE, return low probability
                probability = 0.1

            return float(probability)

        except Exception as e:
            logger.error(f"Error in Smart Turn prediction: {e}", exc_info=True)
            # When an error occurs, return medium probability
            return 0.5

