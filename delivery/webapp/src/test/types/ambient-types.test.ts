import { describe, it, expectTypeOf } from "vitest";
import type {
  PresentationCommand,
  PresentationStatus,
} from "@/types/presentation-api";

/**
 * Compile-only guard for the ambient `.d.ts` declarations added in Task 1
 * (src/types/presentation-api.d.ts and src/types/cast-sdk.d.ts).
 *
 * No runtime assertions are intended: the test exists so that removing the
 * .d.ts files (or breaking the wire-contract shapes) fails the type-check /
 * vitest collect step. Vitest still requires at least one `it(...)` to count
 * as a passing suite — the bodies below are intentionally trivial and exist
 * only to anchor `expectTypeOf` usage.
 */

describe("ambient type declarations", () => {
  it("PresentationCommand union exposes the six variants", () => {
    type VariantTag = PresentationCommand["type"];
    expectTypeOf<VariantTag>().toEqualTypeOf<
      "play" | "pause" | "seek" | "volume" | "mute" | "songTitle"
    >();

    const play: PresentationCommand = { type: "play" };
    const pause: PresentationCommand = { type: "pause" };
    const seek: PresentationCommand = { type: "seek", positionSeconds: 12.5 };
    const volume: PresentationCommand = { type: "volume", level: 0.6 };
    const mute: PresentationCommand = { type: "mute", muted: true };
    const songTitle: PresentationCommand = { type: "songTitle", title: "Amazing Grace" };

    expectTypeOf(play).toMatchTypeOf<PresentationCommand>();
    expectTypeOf(seek.positionSeconds).toEqualTypeOf<number>();
    expectTypeOf(volume.level).toEqualTypeOf<number>();
    expectTypeOf(mute.muted).toEqualTypeOf<boolean>();
    expectTypeOf(songTitle.title).toEqualTypeOf<string>();

    void [play, pause];
  });

  it("PresentationStatus union exposes ready / disconnected / error", () => {
    type VariantTag = PresentationStatus["type"];
    expectTypeOf<VariantTag>().toEqualTypeOf<"ready" | "disconnected" | "error">();

    const ready: PresentationStatus = { type: "ready" };
    const disconnected: PresentationStatus = { type: "disconnected" };
    const error: PresentationStatus = { type: "error", message: "load rejected" };

    expectTypeOf(ready).toMatchTypeOf<PresentationStatus>();
    expectTypeOf(error.message).toEqualTypeOf<string>();

    void disconnected;
  });

  it("chrome.cast namespace is globally available", () => {
    // Type-only assertions: the SDK runtime is loaded lazily in the browser,
    // so we never touch the value space here. These checks fail the type
    // checker if any of the ambient members are removed or renamed.

    expectTypeOf<typeof chrome.cast.media.MediaInfo>().toBeConstructibleWith(
      "https://example.test/v.mp4",
      "video/mp4",
    );
    type MediaInfoField = keyof chrome.cast.media.MediaInfo;
    type ExpectedMediaInfo = "contentId" | "contentType" | "metadata" | "streamType";
    type MissingMediaInfo = Exclude<ExpectedMediaInfo, MediaInfoField> extends never ? true : false;
    expectTypeOf<MissingMediaInfo>().toEqualTypeOf<true>();

    expectTypeOf<typeof chrome.cast.media.LoadRequest>().toBeConstructibleWith(
      {} as chrome.cast.media.MediaInfo,
    );
    type LoadRequestField = keyof chrome.cast.media.LoadRequest;
    type ExpectedLoadRequest = "media" | "currentTime" | "customData";
    type MissingLoadRequest = Exclude<ExpectedLoadRequest, LoadRequestField> extends never ? true : false;
    expectTypeOf<MissingLoadRequest>().toEqualTypeOf<true>();

    expectTypeOf<typeof chrome.cast.Session.prototype.loadMedia>().toBeFunction();
    type LoadMediaArgs = Parameters<chrome.cast.Session["loadMedia"]>;
    type FirstArg = LoadMediaArgs[0];
    type IsLoadRequest = FirstArg extends chrome.cast.media.LoadRequest ? true : false;
    expectTypeOf<IsLoadRequest>().toEqualTypeOf<true>();

    expectTypeOf<typeof chrome.cast.DEFAULT_MEDIA_RECEIVER_APP_ID>().toEqualTypeOf<string>();
    expectTypeOf<typeof chrome.cast.isAvailable>().toEqualTypeOf<boolean>();

    type AutoJoinPolicyValues = `${chrome.cast.AutoJoinPolicy}`;
    type ExpectedAutoJoin = "origin_scoped" | "tab_and_origin_scoped" | "page_scoped";
    type MissingAutoJoin = Exclude<ExpectedAutoJoin, AutoJoinPolicyValues> extends never ? true : false;
    expectTypeOf<MissingAutoJoin>().toEqualTypeOf<true>();

    type StreamTypeValues = `${chrome.cast.StreamType}`;
    type ExpectedStreamType = "buffered" | "live" | "other";
    type MissingStreamType = Exclude<ExpectedStreamType, StreamTypeValues> extends never ? true : false;
    expectTypeOf<MissingStreamType>().toEqualTypeOf<true>();

    const sampleLoadRequest = {
      media: {
        contentId: "https://example.test/v.mp4",
        contentType: "video/mp4",
        metadata: { title: "Test Title" },
        streamType: "buffered" as chrome.cast.StreamType,
      },
      currentTime: 0,
    } as chrome.cast.media.LoadRequest;
    expectTypeOf(sampleLoadRequest.media).toMatchTypeOf<chrome.cast.media.MediaInfo>();

    void sampleLoadRequest;
  });

  it("cast.framework namespace is globally available", () => {
    // Type-only assertions: the SDK runtime is loaded lazily in the browser.

    expectTypeOf<typeof cast.framework.CastContext.getInstance>().toBeFunction();
    type CastContextProto = typeof cast.framework.CastContext.prototype;
    type CastContextMethods = keyof Pick<
      CastContextProto,
      "setOptions" | "addEventListener" | "endCurrentSession" | "requestSession" | "removeEventListener" | "getCastState" | "getCurrentSession"
    >;
    type ExpectedMethods = "setOptions" | "addEventListener" | "endCurrentSession" | "requestSession" | "removeEventListener";
    type MissingMethods = Exclude<ExpectedMethods, CastContextMethods> extends never ? true : false;
    expectTypeOf<MissingMethods>().toEqualTypeOf<true>();

    type FieldKeys = keyof cast.framework.RemotePlayer;
    type ExpectedVisible = "currentTime" | "duration" | "volume" | "isMuted";
    type ExpectedOptional =
      | "playerState"
      | "displayName"
      | "isMediaLoaded"
      | "canPause"
      | "canSeek";
    type MissingVisible = Exclude<ExpectedVisible, FieldKeys> extends never ? true : false;
    type MissingOptional = Exclude<ExpectedOptional, FieldKeys> extends never ? true : false;
    expectTypeOf<MissingVisible>().toEqualTypeOf<true>();
    expectTypeOf<MissingOptional>().toEqualTypeOf<true>();

    expectTypeOf<typeof cast.framework.RemotePlayerController.prototype.play>().toBeFunction();
    type ControllerProto = typeof cast.framework.RemotePlayerController.prototype;
    type ControllerMethods = keyof Pick<
      ControllerProto,
      | "addEventListener"
      | "removeEventListener"
      | "play"
      | "pause"
      | "seek"
      | "setVolumeLevel"
      | "playOrPause"
      | "muteOrUnmute"
    >;
    type ExpectedController =
      | "addEventListener"
      | "removeEventListener"
      | "play"
      | "pause"
      | "seek"
      | "setVolumeLevel"
      | "playOrPause"
      | "muteOrUnmute";
    type MissingController = Exclude<ExpectedController, ControllerMethods> extends never ? true : false;
    expectTypeOf<MissingController>().toEqualTypeOf<true>();

    type RemotePlayerEventValues = `${cast.framework.RemotePlayerEventType}`;
    type ExpectedEvents =
      | "currentTimeChanged"
      | "playerStateChanged"
      | "isMediaLoadedChanged"
      | "volumeLevelChanged"
      | "isMutedChanged"
      | "isConnectedChanged";
    type MissingEvents = Exclude<ExpectedEvents, RemotePlayerEventValues> extends never ? true : false;
    expectTypeOf<MissingEvents>().toEqualTypeOf<true>();

    const sampleOpts = {
      receiverApplicationId: "DEFAULT" as string,
      autoJoinPolicy: "tab_and_origin_scoped" as chrome.cast.AutoJoinPolicy,
      androidReceiverCompatible: true,
    } as cast.framework.CastOptions;
    expectTypeOf(sampleOpts).toMatchTypeOf<cast.framework.CastOptions>();

    void sampleOpts;
  });
});
