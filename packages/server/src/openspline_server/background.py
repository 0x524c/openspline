"""Bounded background decoding and compositing using the portrait's alpha matte."""

import io
import math
import threading

import av
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BACKGROUND_BYTES = 25 * 1024 * 1024


class Background:
    def __init__(self, raw, alpha, bounds):
        self.alpha = np.asarray(alpha, dtype=np.float32)[..., None] / 255
        self.bounds = bounds
        self.container = None
        self.still = None
        self.lock = threading.Lock()
        try:
            picture = Image.open(io.BytesIO(raw))
            if picture.width * picture.height > 16_000_000:
                raise ValueError("Background limit is 16 megapixels")
            self.still = self._fit(ImageOps.exif_transpose(picture).convert("RGB"))
            return
        except UnidentifiedImageError:
            pass
        # Force a supported container; uploaded playlists must never resolve URLs.
        if raw[4:8] == b"ftyp":
            container_format = "mp4"
        elif raw[:4] == b"\x1a\x45\xdf\xa3":
            container_format = "matroska"
        else:
            raise ValueError("Choose a background image, MP4, or WebM video")
        try:
            self.container = av.open(io.BytesIO(raw), format=container_format)
            self.stream = self.container.streams.video[0]
            if self.stream.width * self.stream.height > 16_000_000:
                raise ValueError("Background limit is 16 megapixels")
            duration = (
                float(self.stream.duration * self.stream.time_base)
                if self.stream.duration is not None
                else (self.container.duration or 0) / av.time_base
            )
            if not math.isfinite(duration) or not 0 < duration <= 60:
                raise ValueError("Background videos must be at most 60 seconds long")
            rate = float(self.stream.average_rate or 25)
            if not 0 < rate <= 120:
                raise ValueError("Background videos must be at most 120 fps")
            self.duration = duration
            self.start = float((self.stream.start_time or 0) * self.stream.time_base)
            self._rewind()
        except Exception as exc:
            self.close()
            raise ValueError(f"Cannot open background image or video: {exc}") from exc

    def _fit(self, picture):
        left, top, width, height = self.bounds
        result = Image.new("RGB", (512, 512))
        result.paste(ImageOps.fit(picture, (width, height), Image.Resampling.LANCZOS), (left, top))
        return np.asarray(result)

    def _rewind(self):
        self.container.seek(0)
        self.decoder = iter(self.container.decode(video=0))
        first = next(self.decoder)
        self.current = self._fit(first.to_image())
        self.next = next(self.decoder, None)
        self.position = 0.0

    def render(self, foreground, seconds):
        # A decoder retains only the current and next frame, never the entire clip.
        with self.lock:
            background = self.still
            if background is None:
                target = max(0, seconds) % self.duration
                if target < self.position:
                    self._rewind()
                while self.next is not None and float(self.next.time or 0) - self.start <= target:
                    self.current = self._fit(self.next.to_image())
                    self.next = next(self.decoder, None)
                self.position = target
                background = self.current
            return np.rint(foreground * self.alpha + background * (1 - self.alpha)).astype(np.uint8)

    def close(self):
        with self.lock:
            if self.container is not None:
                self.container.close()
                self.container = None
