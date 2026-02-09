import math
from array import array


class EndpointVAD:
    def __init__(
        self,
        *,
        frame_ms: int,
        rms_threshold: int,
        min_speech_ms: int,
        end_silence_ms: int,
    ) -> None:
        self.frame_ms = max(10, frame_ms)
        self.rms_threshold = max(1, rms_threshold)
        self.min_speech_ms = max(self.frame_ms, min_speech_ms)
        self.end_silence_ms = max(self.frame_ms, end_silence_ms)
        self._speech_ms = 0
        self._silence_ms = 0
        self._has_started = False

    @property
    def has_started(self) -> bool:
        return self._has_started

    def update(self, frame: bytes) -> tuple[bool, bool, int]:
        if not frame:
            return self._has_started, False, 0

        samples = array("h")
        samples.frombytes(frame)
        if not samples:
            return self._has_started, False, 0

        square_sum = 0
        for sample in samples:
            square_sum += sample * sample
        rms = int(math.sqrt(square_sum / len(samples)))
        is_speech = rms >= self.rms_threshold

        if is_speech:
            self._speech_ms += self.frame_ms
            self._silence_ms = 0
        else:
            if self._has_started:
                self._silence_ms += self.frame_ms

        if not self._has_started and self._speech_ms >= self.min_speech_ms:
            self._has_started = True

        endpoint = self._has_started and self._silence_ms >= self.end_silence_ms
        return self._has_started, endpoint, rms
