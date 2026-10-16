import asyncio
import base64
import io
from fractions import Fraction
from types import SimpleNamespace

import av
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from openspline import Openspline
from openspline_server.app import create_app
from openspline_server.background import Background
from openspline_server.config import Settings
from openspline_server.media import Timeline, VideoTrack
from openspline_server.socket_playback import SocketPlayback
from PIL import Image


def png(color, mode="RGB"):
    output = io.BytesIO()
    Image.new(mode, (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def clip():
    output = io.BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        for i, color in enumerate(["red", "blue"]):
            frame = av.VideoFrame.from_image(Image.new("RGB", (32, 32), color))
            frame.pts, frame.time_base = i, Fraction(1, 2)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return output.getvalue()


def test_image_alpha_and_partial_transparency_stay_still():
    alpha = Image.new("L", (512, 512), 0)
    alpha.paste(255, (100, 100, 200, 200))
    alpha.paste(128, (200, 200, 300, 300))
    bg = Background(png("blue"), alpha, (0, 0, 512, 512))
    foreground = np.full((512, 512, 3), [255, 0, 0], np.uint8)
    frame = bg.render(foreground, 0)
    np.testing.assert_array_equal(frame[0, 0], [0, 0, 255])
    np.testing.assert_array_equal(frame[150, 150], [255, 0, 0])
    np.testing.assert_array_equal(frame[250, 250], [128, 0, 127])
    np.testing.assert_array_equal(frame, bg.render(foreground, 1000))
    bg.close()


def test_video_loops_and_rewinds_without_loading_entire_clip():
    bg = Background(clip(), Image.new("L", (512, 512)), (0, 0, 512, 512))
    foreground = np.zeros((512, 512, 3), np.uint8)
    try:
        first = bg.render(foreground, 0)
        second = bg.render(foreground, 0.6)
        assert first[0, 0, 0] > 240 and second[0, 0, 2] > 240
        np.testing.assert_array_equal(first, bg.render(foreground, 1.1))
        np.testing.assert_array_equal(second, bg.render(foreground, 2.6))
    finally:
        bg.close()
    assert bg.container is None


async def test_webrtc_and_socket_composite_even_without_idle_inference():
    bg = Background(png("blue"), Image.new("L", (512, 512)), (0, 0, 512, 512))
    timeline = Timeline(np.zeros((512, 512, 3), np.uint8), bg)
    track = VideoTrack(timeline)
    frame = await track.recv()
    np.testing.assert_array_equal(frame.to_ndarray(format="rgb24")[0, 0], [0, 0, 255])
    ready = asyncio.Event()
    ready.set()
    session = SimpleNamespace(
        timeline=timeline, viewer_ready=ready, epoch=0, closed=False, frame_content=(0, 0, 512, 512)
    )
    packets = []

    class Socket:
        async def send_json(self, event):
            packets.append(event)
            if event["type"] == "idle":
                session.closed = True

        async def close(self, **kwargs):
            pass

    await asyncio.wait_for(SocketPlayback(session, Socket()).send(), 2)
    image = Image.open(io.BytesIO(base64.b64decode(packets[-1]["image"])))
    assert image.getpixel((0, 0))[2] > 250
    bg.close()
    track.stop()


def test_api_preserves_portrait_alpha_and_rejects_bad_background_before_reservation(tmp_path):
    app = create_app(Settings(backend="test", runtime_dir=str(tmp_path)))
    with TestClient(app) as client:
        files = {
            "portrait": ("portrait.png", png((255, 0, 0, 0), "RGBA")),
            "background": ("background", b"not media"),
        }
        assert client.post("/v1/sessions", files=files).status_code == 422
        assert not client.get("/readyz").json()["workers"][0]["occupied"]
        files["background"] = ("background.png", png("blue"))
        response = client.post("/v1/sessions", files=files)
        assert response.status_code == 201, response.text
        descriptor = response.json()
        url = f"/v1/sessions/{descriptor['id']}"
        poster = client.get(
            url + "/portrait", headers={"Authorization": "Bearer " + descriptor["token"]}
        )
        assert Image.open(io.BytesIO(poster.content)).getpixel((256, 256))[2] > 250
        session = app.state.sessions[descriptor["id"]]
        assert session.timeline.background is not None
        client.delete(url, headers={"Authorization": "Bearer " + descriptor["publisher_token"]})
        assert not client.get("/readyz").json()["workers"][0]["occupied"]


@pytest.mark.parametrize("from_path", [False, True])
async def test_python_sdk_uploads_optional_background(monkeypatch, tmp_path, from_path):
    async def post(self, path, **kwargs):
        assert kwargs["files"]["background"][1] == b"background-bytes"
        assert kwargs["files"]["portrait"][1] == b"portrait-bytes"
        raise RuntimeError("captured upload")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    background = b"background-bytes"
    if from_path:
        background = tmp_path / "background.mp4"
        background.write_bytes(b"background-bytes")
    with pytest.raises(RuntimeError, match="captured upload"):
        async with Openspline().avatar(b"portrait-bytes", background=background):
            pass
