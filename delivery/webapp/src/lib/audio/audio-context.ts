/**
 * One shared AudioContext for every browser audio surface (ADR-0012: browser
 * audio engine decodes clips, not media elements).
 *
 * No `"use client"` directive: browser globals are touched only inside these
 * function bodies, so importing this module during server rendering is safe.
 */

let audioContext: AudioContext | null = null;

/**
 * Returns the app-wide AudioContext, creating it on first use.
 *
 * No options are passed: iOS Safari rejects `new AudioContext({ sampleRate })`.
 * The context is never closed, so every audio surface shares one clock and one
 * destination.
 */
export function getAudioContext(): AudioContext {
  if (!audioContext) {
    audioContext = new AudioContext();
  }
  return audioContext;
}

/**
 * Unlocks the shared AudioContext for playback.
 *
 * Callers MUST invoke this synchronously inside the click handler that starts
 * playback (iOS/Chrome gesture unlock). A call made after an `await` is no
 * longer inside the user gesture and does not unlock audio.
 */
export function unlockAudioContext(): void {
  const context = getAudioContext();
  void context.resume().catch(() => {
    // A rejected resume just means the caller was not inside a gesture; it
    // must not surface as an unhandled rejection. Repeated calls stay safe.
  });
  const source = context.createBufferSource();
  // One silent sample: enough to flip the context out of "suspended" on iOS
  // and Chrome without producing audible output.
  source.buffer = context.createBuffer(1, 1, context.sampleRate);
  source.connect(context.destination);
  source.start();
}
