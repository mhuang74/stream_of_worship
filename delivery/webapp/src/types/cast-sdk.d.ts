// Ambient Google Cast Web Sender SDK global surface.
//
// Loaded by `src/lib/cast/loader.ts` via the external
// https://www.gstatic.com/cv/js/sender/v1/cast_sender.js script, which sets
// `window.chrome.cast` and `window.cast.framework`. This file declares the
// narrow subset used by `src/hooks/useCast.ts` (Task 3) and the dispatcher:
//
//   - chrome.cast.media.MediaInfo / LoadRequest
//   - chrome.cast.Session.loadMedia
//   - cast.framework.CastContext (getInstance, setOptions, requestSession,
//     endCurrentSession, addEventListener)
//   - cast.framework.RemotePlayer (read fields)
//   - cast.framework.RemotePlayerController (control methods + event listeners)
//
// Reference: https://developers.google.com/cast/docs/web_sender
//
// This file is intentionally ambient (no top-level `export`/`import`) so the
// namespaces augment the global scope. Narrow to the event types used by the
// hook: CURRENT_TIME_CHANGED, PLAYER_STATE_CHANGED, IS_MEDIA_LOADED_CHANGED,
// VOLUME_LEVEL_CHANGED, IS_MUTED_CHANGED, IS_CONNECTED_CHANGED.
// Additional Cast SDK members (e.g. tracks, queues, custom data classes) are
// omitted on purpose and should be added here when first needed.

declare namespace chrome.cast {
  export const VERSION: number;
  /** True after the Cast SDK script finishes loading and globalizes this ns. */
  export const isAvailable: boolean;
  /** Google Default Media Receiver application id (used when no custom id set). */
  export const DEFAULT_MEDIA_RECEIVER_APP_ID: string;

  export enum AutoJoinPolicy {
    ORIGIN_SCOPED = "origin_scoped",
    TAB_AND_ORIGIN_SCOPED = "tab_and_origin_scoped",
    PAGE_SCOPED = "page_scoped",
  }

  export enum Capability {
    VIDEO_OUT = "video_out",
    AUDIO_OUT = "audio_out",
    VIDEO_IN = "video_in",
    AUDIO_IN = "audio_in",
  }

  export enum StreamType {
    BUFFERED = "buffered",
    LIVE = "live",
    OTHER = "other",
  }

  /** Structured error passed to Cast SDK failure callbacks. */
  export interface Error {
    code: string;
    description?: string;
    details?: unknown;
  }

  export namespace media {
    /** Plain metadata used for the `title` shown in the receiver UI. */
    export class GenericMediaMetadata {
      title?: string;
      subtitle?: string;
      images?: { url: string; width?: number; height?: number }[];
      releaseDate?: string;
    }

    /** Metadata type discriminator used by MediaInfo.metadata. */
    export enum MetadataType {
      GENERIC = 0,
      MOVIE = 1,
      TV_SHOW = 2,
      MUSIC_TRACK = 3,
      PHOTO = 4,
      AUDIOBOOK_CHAPTER = 5,
    }

    /** Description of the media the receiver should load. */
    export class MediaInfo {
      constructor(contentId: string, contentType: string);
      contentId: string;
      contentType: string;
      metadata?: GenericMediaMetadata & { metadataType?: MetadataType };
      streamType?: StreamType | string;
      duration?: number | null;
      customData?: Record<string, unknown> | null;
      textTrackStyle?: unknown;
      tracks?: unknown[];
    }

    /** Passed to Session.loadMedia to start playback at a given time. */
    export class LoadRequest {
      constructor(media: MediaInfo);
      media: MediaInfo;
      currentTime?: number;
      customData?: Record<string, unknown> | null;
      activeTrackIds?: number[] | null;
      playbackRate?: number;
    }
  }

  /** Active session between sender and a receiver device. */
  export class Session {
    sessionId: string;
    appId: string;
    displayName: string;
    statusText: string;
    /**
     * Loads media into the receiver. The newer overloaded form accepts a
     * `media.LoadRequest` (with `currentTime` for resume). Callbacks follow
     * the Cast convention of (onSuccess, onError) — no Promise form.
     */
    loadMedia(
      loadRequest: media.LoadRequest,
      onSuccess: () => void,
      onError: (error: chrome.cast.Error) => void,
    ): void;
    stop(onSuccess: () => void, onError: (error: chrome.cast.Error) => void): void;
    setVolume(level: number, onSuccess: () => void, onError: (error: chrome.cast.Error) => void): void;
    addUpdateListener(listener: (isAlive: boolean) => void): void;
  }
}

declare namespace cast.framework {
  /** Coarse Cast button state (used for sender availability hints). */
  export enum CastState {
    NO_DEVICES_AVAILABLE = "NO_DEVICES_AVAILABLE",
    NOT_CONNECTED = "NOT_CONNECTED",
    CONNECTING = "CONNECTING",
    CONNECTED = "CONNECTED",
  }

  export enum SessionState {
    NO_SESSION = "NO_SESSION",
    SESSION_STARTING = "SESSION_STARTING",
    SESSION_STARTED = "SESSION_STARTED",
    SESSION_START_FAILED = "SESSION_START_FAILED",
    SESSION_ENDING = "SESSION_ENDING",
    SESSION_ENDED = "SESSION_ENDED",
    SESSION_RESUMED = "SESSION_RESUMED",
  }

  /** CastContext-level events surfaced via addEventListener. */
  export enum CastContextEventType {
    CAST_STATE_CHANGED = "caststatechanged",
    SESSION_STATE_CHANGED = "sessionstatechanged",
  }

  /**
   * RemotePlayerController event types. Narrow to the ones used by the
   * transport hook — adding more is allowed, but the six listed below are
   * the ones whose channels are observed in `useCast.ts`.
   */
  export enum RemotePlayerEventType {
    CURRENT_TIME_CHANGED = "currentTimeChanged",
    PLAYER_STATE_CHANGED = "playerStateChanged",
    IS_MEDIA_LOADED_CHANGED = "isMediaLoadedChanged",
    VOLUME_LEVEL_CHANGED = "volumeLevelChanged",
    IS_MUTED_CHANGED = "isMutedChanged",
    IS_CONNECTED_CHANGED = "isConnectedChanged",
  }

  /** Options handed to CastContext.setOptions(). */
  export interface CastOptions {
    receiverApplicationId?: string;
    autoJoinPolicy?: chrome.cast.AutoJoinPolicy | string;
    androidReceiverCompatible?: boolean;
    resumeSavedSession?: boolean;
    language?: string;
    credentialsData?: { credentials: string; credentialsType?: string };
  }

  /** A status-changed event payload emitted by CastContext. */
  export interface CastStateEventData {
    type: CastContextEventType;
    castState?: CastState;
    sessionState?: SessionState;
    session?: chrome.cast.Session | null;
  }

  /** A status-changed event payload emitted by RemotePlayerController. */
  export interface RemotePlayerChangedEvent {
    type: RemotePlayerEventType;
    field?: string;
    value?: unknown;
  }

  /**
   * Singleton sender-side bridge to the receiver. `getInstance()` is the only
   * sanctioned entry point — never `new CastContext()`.
   */
  export class CastContext {
    static getInstance(): CastContext;
    setOptions(options: CastOptions): void;
    getCastState(): CastState;
    getSessionState(): SessionState;
    /** Triggers the device-picker dialog (must run in a user-gesture). */
    requestSession(): Promise<void>;
    endCurrentSession(stopCasting?: boolean): void;
    addEventListener(
      type: CastContextEventType,
      handler: (event: CastStateEventData) => void,
    ): void;
    removeEventListener(
      type: CastContextEventType,
      handler: (event: CastStateEventData) => void,
    ): void;
    getCurrentSession(): chrome.cast.Session | null;
  }

  /**
   * Read-mostly mirror of the receiver's playback state. Mutated by the SDK
   * in place — properties reflect the latest receiver status. Construct via
   * `new RemotePlayer()` and pair with a RemotePlayerController.
   */
  export class RemotePlayer {
    currentTime: number;
    duration: number;
    volume: number;
    isMediaLoaded: boolean;
    isMuted: boolean;
    playerState: string;
    displayName: string;
    canPause: boolean;
    canSeek: boolean;
    isConnected: boolean;
    isPaused: boolean;
    title: string;
    displayStatus: string;
  }

  /**
   * Issues playback control optns to the receiver, and delivers
   * RemotePlayer property-diff events. One controller per RemotePlayer.
   */
  export class RemotePlayerController {
    constructor(player: RemotePlayer);
    addEventListener(
      type: RemotePlayerEventType,
      handler: (event: RemotePlayerChangedEvent) => void,
    ): void;
    removeEventListener(
      type: RemotePlayerEventType,
      handler: (event: RemotePlayerChangedEvent) => void,
    ): void;
    play(): void;
    pause(): void;
    seek(): void;
    stop(): void;
    setVolumeLevel(volume: number): void;
    playOrPause(): void;
    muteOrUnmute(): void;
    getFormattedTime(timeInSec: number): string;
    getSeekPosition(currentTime: number, duration: number): number;
    getSeekTime(currentPosition: number, duration: number): number;
  }
}
