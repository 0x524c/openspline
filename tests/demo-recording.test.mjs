import test from 'node:test';
import assert from 'node:assert/strict';
import {createDemoRecording} from '../packages/server/src/openspline_server/static/demo-recording.js';

class Recorder {
  static isTypeSupported(type) {return type === 'video/webm;codecs=vp8,opus';}
  constructor(stream, options) {
    this.stream = stream;
    this.mimeType = options.mimeType;
    this.state = 'inactive';
    Recorder.last = this;
  }
  start() {this.state = 'recording';}
  stop() {
    this.state = 'inactive';
    this.ondataavailable({data: new Blob(['video'], {type: this.mimeType})});
    this.onstop();
  }
}

function connection() {
  return Object.assign(new EventTarget(), {createRecordingStream: () => ({tracks: ['video', 'audio']})});
}

test('records from first generated media through playback completion', async () => {
  const playback = connection();
  const recording = createDemoRecording(playback, Recorder);
  assert.equal(Recorder.last.state, 'inactive');
  assert.deepEqual(Recorder.last.stream.tracks, ['video', 'audio']);
  playback.dispatchEvent(new Event('media'));
  assert.equal(Recorder.last.state, 'recording');
  const video = await recording.finish();
  assert.equal(Recorder.last.state, 'inactive');
  assert.equal(video.type, 'video/webm;codecs=vp8,opus');
  assert.equal(await video.text(), 'video');
});

test('ending a session cancels recording before media arrives', async () => {
  const playback = connection();
  const recording = createDemoRecording(playback, Recorder);
  recording.cancel();
  playback.dispatchEvent(new Event('media'));
  assert.equal(Recorder.last.state, 'inactive');
  await assert.rejects(recording.finish(), /No video was generated/);
});

test('ending a session stops an active recording', async () => {
  const playback = connection();
  const recording = createDemoRecording(playback, Recorder);
  playback.dispatchEvent(new Event('media'));
  recording.cancel();
  assert.equal(Recorder.last.state, 'inactive');
  await assert.rejects(recording.finish(), /No video was generated/);
});
