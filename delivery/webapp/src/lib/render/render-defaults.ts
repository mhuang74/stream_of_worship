import { normalizeFontFamily, type FontFamilyValue } from "@/lib/constants"
import type { RenderFormData } from "@/components/render/RenderForm"

export const APP_RENDER_DEFAULTS: Partial<RenderFormData> = {
  template: "dark",
  resolution: "720p",
  fontSizePreset: "M",
  fontFamily: "noto_serif_tc",
  audioEnabled: true,
  videoEnabled: true,
  includeTitleCard: false,
  titleCardDurationSeconds: 10,
  titleCardLines: [],
  offlineEnabled: false,
}

export interface UserSettingsData {
  defaultVideoTemplate?: string
  defaultResolution?: string
  defaultFontSizePreset?: string
  defaultFontFamily?: string
}

export function buildInitialRenderData(
  latestJob: Record<string, unknown> | null,
  userSettings: UserSettingsData | null
): Partial<RenderFormData> {
  const result: Partial<RenderFormData> = { ...APP_RENDER_DEFAULTS }

  if (userSettings) {
    if (userSettings.defaultVideoTemplate) result.template = userSettings.defaultVideoTemplate as RenderFormData["template"]
    if (userSettings.defaultResolution) result.resolution = userSettings.defaultResolution as RenderFormData["resolution"]
    if (userSettings.defaultFontSizePreset) result.fontSizePreset = userSettings.defaultFontSizePreset as RenderFormData["fontSizePreset"]
    if (userSettings.defaultFontFamily) result.fontFamily = normalizeFontFamily(userSettings.defaultFontFamily)
  }

  if (latestJob) {
    if (latestJob.template) result.template = latestJob.template as RenderFormData["template"]
    if (latestJob.resolution) result.resolution = latestJob.resolution as RenderFormData["resolution"]
    if (latestJob.fontSizePreset) result.fontSizePreset = latestJob.fontSizePreset as RenderFormData["fontSizePreset"]
    if (latestJob.fontFamily != null) result.fontFamily = normalizeFontFamily(latestJob.fontFamily) as FontFamilyValue
    if (latestJob.audioEnabled != null) result.audioEnabled = latestJob.audioEnabled as boolean
    if (latestJob.videoEnabled != null) result.videoEnabled = latestJob.videoEnabled as boolean
    if (latestJob.includeTitleCard != null) result.includeTitleCard = latestJob.includeTitleCard as boolean
    if (latestJob.titleCardDurationSeconds != null) result.titleCardDurationSeconds = latestJob.titleCardDurationSeconds as number
    if (latestJob.titleCardLines) result.titleCardLines = latestJob.titleCardLines as string[]
  }

  return result
}
