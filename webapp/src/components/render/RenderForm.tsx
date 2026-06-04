"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { AlertCircle, Info } from "lucide-react"
import Link from "next/link"
import { FONT_FAMILIES, type FontFamilyValue } from "@/lib/constants"

export interface RenderFormData {
  audioEnabled: boolean
  videoEnabled: boolean
  template: "dark" | "gradient_warm" | "gradient_blue"
  resolution: "720p" | "1080p"
  fontSizePreset: "S" | "M" | "L" | "XL"
  fontFamily: FontFamilyValue
  includeTitleCard: boolean
  titleCardDurationSeconds: number
  titleCardLines: string[]
  offlineEnabled: boolean
}

interface RenderFormProps {
  songsetId: string
  initialData?: Partial<RenderFormData>
  markedLineCount?: number
  songsetName?: string
  songTitles?: string[]
  onSubmit: (data: RenderFormData) => void
  onCancel: () => void
  isSubmitting?: boolean
}

const TEMPLATES = [
  { value: "dark", label: "Dark" },
  { value: "gradient_warm", label: "Gradient Warm" },
  { value: "gradient_blue", label: "Gradient Blue" },
] as const

const RESOLUTIONS = [
  { value: "720p", label: "720p (HD)" },
  { value: "1080p", label: "1080p (Full HD)" },
] as const

const FONT_SIZES = [
  { value: "S", label: "Small (32px)", px: 32 },
  { value: "M", label: "Medium (48px)", px: 48 },
  { value: "L", label: "Large (64px)", px: 64 },
  { value: "XL", label: "Extra Large (80px)", px: 80 },
] as const

const TITLE_CARD_DURATIONS = [
  { value: 5, label: "5 seconds" },
  { value: 10, label: "10 seconds" },
  { value: 15, label: "15 seconds" },
  { value: 20, label: "20 seconds" },
  { value: 25, label: "25 seconds" },
  { value: 30, label: "30 seconds" },
] as const

function isIOS174OrLater(): boolean {
  if (typeof navigator === "undefined") return false
  const userAgent = navigator.userAgent
  
  // Check if iOS
  const isIOS = /iPad|iPhone|iPod/.test(userAgent)
  if (!isIOS) return true // Not iOS, so no restriction
  
  // Extract iOS version
  const match = userAgent.match(/OS (\d+)_(\d+)/)
  if (!match) return false
  
  const major = parseInt(match[1], 10)
  const minor = parseInt(match[2], 10)
  
  // iOS 17.4 or later
  return major > 17 || (major === 17 && minor >= 4)
}

export function RenderForm({
  songsetId,
  initialData,
  markedLineCount = 0,
  songsetName,
  songTitles,
  onSubmit,
  onCancel,
  isSubmitting = false,
}: RenderFormProps) {
  const [formData, setFormData] = useState<RenderFormData>({
    audioEnabled: initialData?.audioEnabled ?? true,
    videoEnabled: initialData?.videoEnabled ?? true,
    template: initialData?.template ?? "dark",
    resolution: initialData?.resolution ?? "720p",
    fontSizePreset: initialData?.fontSizePreset ?? "M",
    fontFamily: initialData?.fontFamily ?? "noto_serif_tc",
    includeTitleCard: initialData?.includeTitleCard ?? false,
    titleCardDurationSeconds: initialData?.titleCardDurationSeconds ?? 10,
    titleCardLines: initialData?.titleCardLines ?? [],
    offlineEnabled: initialData?.offlineEnabled ?? false,
  })

  const iosSupportsOffline = isIOS174OrLater()

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit(formData)
  }

  const updateField = <K extends keyof RenderFormData>(
    field: K,
    value: RenderFormData[K]
  ) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
  }

  return (
    <TooltipProvider>
      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Output Options */}
        <Card>
          <CardHeader>
            <CardTitle>Output Options</CardTitle>
            <CardDescription>Choose what to render</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="audio">Audio (MP3)</Label>
                <p className="text-sm text-muted-foreground">
                  Mixed audio with transitions
                </p>
              </div>
              <Switch
                id="audio"
                checked={formData.audioEnabled}
                onCheckedChange={(checked) =>
                  updateField("audioEnabled", checked)
                }
              />
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="video">Video (MP4)</Label>
                <p className="text-sm text-muted-foreground">
                  Lyrics video with audio
                </p>
              </div>
              <Switch
                id="video"
                checked={formData.videoEnabled}
                onCheckedChange={(checked) =>
                  updateField("videoEnabled", checked)
                }
              />
            </div>
          </CardContent>
        </Card>

        {/* Video Settings */}
        {formData.videoEnabled && (
          <Card>
            <CardHeader>
              <CardTitle>Video Settings</CardTitle>
              <CardDescription>Customize the lyrics video</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="template">Template</Label>
                <Select
                  value={formData.template}
                  onValueChange={(value) =>
                    updateField("template", value as RenderFormData["template"])
                  }
                >
                  <SelectTrigger id="template">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TEMPLATES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="resolution">Resolution</Label>
                <Select
                  value={formData.resolution}
                  onValueChange={(value) =>
                    updateField("resolution", value as RenderFormData["resolution"])
                  }
                >
                  <SelectTrigger id="resolution">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RESOLUTIONS.map((r) => (
                      <SelectItem key={r.value} value={r.value}>
                        {r.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="fontSize">Font Size</Label>
                <Select
                  value={formData.fontSizePreset}
                  onValueChange={(value) =>
                    updateField("fontSizePreset", value as RenderFormData["fontSizePreset"])
                  }
                >
                  <SelectTrigger id="fontSize">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FONT_SIZES.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="fontFamily">Font Family</Label>
                <Select
                  value={formData.fontFamily}
                  onValueChange={(value) =>
                    updateField("fontFamily", value as RenderFormData["fontFamily"])
                  }
                >
                  <SelectTrigger id="fontFamily">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FONT_FAMILIES.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div
                  className="rounded-md border border-muted-foreground/20 bg-muted/50 p-3 text-center"
                  style={{
                    fontFamily: `var(${FONT_FAMILIES.find((f) => f.value === formData.fontFamily)?.cssVariable ?? "--font-noto-serif-tc"})`,
                  }}
                >
                  <p className="text-lg">耶和華是我的牧者</p>
                  <p className="text-sm text-muted-foreground">我必不至缺乏</p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Title Card */}
        <Card>
          <CardHeader>
            <CardTitle>Title Card</CardTitle>
            <CardDescription>Add an opening title card</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center space-x-2">
              <Checkbox
                id="includeTitleCard"
                checked={formData.includeTitleCard}
                onCheckedChange={(checked) =>
                  updateField("includeTitleCard", checked as boolean)
                }
              />
              <Label htmlFor="includeTitleCard">Include title card</Label>
            </div>

            {formData.includeTitleCard && (
              <div className="space-y-4 pl-6">
                <div className="space-y-2">
                  <Label htmlFor="titleCardDuration">Duration</Label>
                  <Select
                    value={(formData.titleCardDurationSeconds ?? 10).toString()}
                    onValueChange={(value) =>
                      updateField("titleCardDurationSeconds", parseInt(value ?? "10", 10))
                    }
                  >
                    <SelectTrigger id="titleCardDuration">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TITLE_CARD_DURATIONS.map((d) => (
                        <SelectItem key={d.value} value={d.value.toString()}>
                          {d.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="titleCardLines">Custom title card text</Label>
                  <p className="text-sm text-muted-foreground">
                    One line per entry. Leave empty to use songset name and song titles.
                  </p>
                  <textarea
                    id="titleCardLines"
                    className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    placeholder={"Sunday Morning Worship\nAmazing Grace\nHow Great Thou Art"}
                    value={formData.titleCardLines.join("\n")}
                    onChange={(e) => {
                      const lines = e.target.value.split("\n").filter((line) => line.trim() !== "")
                      updateField("titleCardLines", lines)
                    }}
                  />
                </div>

                {formData.titleCardLines.length === 0 && songTitles && songTitles.length > 0 && (
                  <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/50 p-3">
                    <p className="text-xs text-muted-foreground mb-1">Default title card lines:</p>
                    <p className="text-sm text-muted-foreground whitespace-pre-line">
                      {songsetName || "Worship Set"}{"\n"}
                      {songTitles.join("\n")}
                    </p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Offline Availability */}
        <Card>
          <CardHeader>
            <CardTitle>Offline Availability</CardTitle>
            <CardDescription>Cache for offline playback</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-start space-x-2">
              <Checkbox
                id="offlineEnabled"
                checked={formData.offlineEnabled}
                onCheckedChange={(checked) =>
                  updateField("offlineEnabled", checked as boolean)
                }
                disabled={!iosSupportsOffline}
              />
              <div className="space-y-1 leading-none">
                <div className="flex items-center gap-2">
                  <Label
                    htmlFor="offlineEnabled"
                    className={!iosSupportsOffline ? "text-muted-foreground" : ""}
                  >
                    Make available offline
                  </Label>
                  {!iosSupportsOffline && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="size-4 text-muted-foreground cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Requires iOS 17.4 or later</p>
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">
                  Cache rendered files for offline playback
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Marked Lines Warning */}
        {markedLineCount > 0 && (
          <div className="flex items-start gap-3 rounded-lg border border-yellow-500/20 bg-yellow-500/10 p-4">
            <AlertCircle className="size-5 shrink-0 text-yellow-600" />
            <div className="flex-1 space-y-1">
              <p className="font-medium text-yellow-900 dark:text-yellow-100">
                {markedLineCount} marked line{markedLineCount !== 1 ? "s" : ""} need attention
              </p>
              <p className="text-sm text-yellow-800 dark:text-yellow-200">
                Some lyrics have been marked for review. Please verify before rendering.
              </p>
            </div>
            <Link
              href={`/songsets/${songsetId}`}
              className="text-sm font-medium text-yellow-900 underline underline-offset-4 hover:text-yellow-800 dark:text-yellow-100 dark:hover:text-yellow-200"
            >
              Review
            </Link>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex gap-3 pt-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="flex-1 rounded-lg border border-input bg-background px-4 py-3 text-sm font-medium transition-colors hover:bg-muted disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting || (!formData.audioEnabled && !formData.videoEnabled)}
            className="flex-1 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {isSubmitting ? "Starting..." : "Start Render"}
          </button>
        </div>
      </form>
    </TooltipProvider>
  )
}
