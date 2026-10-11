export function createDemoRecording(connection, Recorder = globalThis.MediaRecorder) {
  if (!Recorder) throw new Error('This browser does not support video recording');
  const types = ['video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus', 'video/webm', 'video/mp4'];
  const mimeType = types.find(type => Recorder.isTypeSupported?.(type));
  const recorder = new Recorder(connection.createRecordingStream(), mimeType ? {mimeType} : undefined);
  const chunks = [];
  let started = false, cancelled = false;
  const complete = new Promise((resolve, reject) => {
    recorder.ondataavailable = event => {if (event.data.size) chunks.push(event.data);};
    recorder.onstop = () => resolve(new Blob(chunks, {type: recorder.mimeType || mimeType}));
    recorder.onerror = event => reject(event.error ?? new Error('Video recording failed'));
  });
  complete.catch(() => {});
  function start() {
    if (cancelled || recorder.state !== 'inactive') return;
    recorder.start(1000);
    started = true;
  }
  connection.addEventListener('media', start, {once: true});
  return {
    async finish() {
      connection.removeEventListener('media', start);
      if (!started || cancelled) throw new Error('No video was generated');
      recorder.stop();
      const video = await complete;
      if (!video.size) throw new Error('The video recording was empty');
      return video;
    },
    cancel() {
      cancelled = true;
      connection.removeEventListener('media', start);
      if (recorder.state === 'recording') recorder.stop();
    },
  };
}
