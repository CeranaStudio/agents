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

"""Pipecat Smart Turn turn detector plugin for LiveKit Agents

This plugin provides a turn detector implementation using Pipecat's Smart Turn model
for end-of-turn detection based on audio analysis.

Installation:
    pip install livekit-plugins-pipecat-smart-turn
    # Also install pipecat-ai:
    pip install pipecat-ai
    # Or from GitHub:
    # pip install git+https://github.com/pipecat-ai/smart-turn.git

Usage:
    from livekit.plugins import pipecat_smart_turn
    from livekit.plugins import silero

    # VAD for speech activity detection
    vad = silero.VAD.load()

    # Turn detector using Pipecat Smart Turn
    turn_detector = pipecat_smart_turn.PipecatSmartTurnDetector()

    session = AgentSession(
        vad=vad,
        turn_detection=turn_detector,
        # ... other config
    )
"""

from livekit.agents import Plugin

from .log import logger
from .turn_detector import PipecatSmartTurnDetector
from .version import __version__

__all__ = ["PipecatSmartTurnDetector", "__version__"]


class PipecatSmartTurnPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(PipecatSmartTurnPlugin())

# Cleanup docs of unexported modules
_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}

for n in NOT_IN_ALL:
    __pdoc__[n] = False

