# Pipecat plugin for LiveKit Agents

This plugin provides a turn detector implementation using Pipecat's Smart Turn model for end-of-turn detection based on audio analysis.

Traditional voice agents use VAD (voice activity detection) for end-of-turn detection. However, VAD models lack language understanding, often causing false positives where the agent interrupts the user before they finish speaking.

By leveraging Pipecat's Smart Turn model, this plugin offers a more accurate and robust method for detecting end-of-turns based on audio analysis.

See [https://docs.livekit.io/agents/](https://docs.livekit.io/agents/) for more information.

## Installation

```bash
pip install livekit-plugins-pipecat
```

## Usage

```python
from livekit.plugins import pipecat
from livekit.plugins import silero

# VAD for speech activity detection
vad = silero.VAD.load()

# Turn detector using Pipecat Smart Turn
turn_detector = pipecat.PipecatSmartTurnDetector()

session = AgentSession(
    vad=vad,
    turn_detection=turn_detector,
    # ... other config
)
```

## Configuration

The `PipecatSmartTurnDetector` supports the following configuration options:

- `model_path`: Path to the Smart Turn model (optional, will use default model if not provided)
- `sample_rate`: Audio sample rate, default 16000 Hz
- `unlikely_threshold`: Probability threshold for end-of-turn detection, below which is considered unlikely to end (default: 0.5)

## License

The plugin source code is licensed under the Apache-2.0 license.

