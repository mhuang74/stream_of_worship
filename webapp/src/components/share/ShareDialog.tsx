"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "sonner";
import { Copy, Trash2, Link, MessageCircle, Mail, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

const SIZE_LIMITS = {
  whatsapp: 2 * 1024 * 1024 * 1024,
  line: 1 * 1024 * 1024 * 1024,
  email: 25 * 1024 * 1024,
};

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatLimit(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) {
    return `${bytes / (1024 * 1024 * 1024)} GB`;
  }
  return `${bytes / (1024 * 1024)} MB`;
}

function formatShareDuration(seconds: number | null): string {
  if (!seconds) return "Not available";
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const mins = totalMinutes % 60;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

export interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  songsetId: string;
  songsetName: string;
  durationSeconds: number | null;
  renderJobId?: string;
}

type Tab = "send-file" | "share-link";

interface ShareInfo {
  token: string;
  shareUrl: string;
}

interface ArtifactSizes {
  mp3SizeBytes: number | null;
  mp4SizeBytes: number | null;
}

export function ShareDialog({
  open,
  onOpenChange,
  songsetId,
  songsetName,
  durationSeconds,
  renderJobId,
}: ShareDialogProps) {
  const [activeTab, setActiveTab] = useState<Tab>("share-link");
  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);
  const [artifactSizes, setArtifactSizes] = useState<ArtifactSizes | null>(null);
  const [isLoadingShare, setIsLoadingShare] = useState(false);
  const [isLoadingSizes, setIsLoadingSizes] = useState(false);
  const [isRevoking, setIsRevoking] = useState(false);

  useEffect(() => {
    if (!open) return;

    async function loadData() {
      setIsLoadingShare(true);
      try {
        const res = await fetch(`/api/share?songsetId=${encodeURIComponent(songsetId)}`);
        if (res.ok) {
          const data = await res.json();
          if (data.shares?.length > 0) {
            const first = data.shares[0];
            setShareInfo({ token: first.token, shareUrl: first.shareUrl });
          }
        }
      } catch {
        // Ignore errors loading existing shares
      } finally {
        setIsLoadingShare(false);
      }

      if (renderJobId) {
        setIsLoadingSizes(true);
        try {
          const res = await fetch(`/api/render-jobs/${renderJobId}/artifact-sizes`);
          if (res.ok) {
            const data = await res.json();
            setArtifactSizes({
              mp3SizeBytes: data.mp3SizeBytes ?? null,
              mp4SizeBytes: data.mp4SizeBytes ?? null,
            });
          }
        } catch {
          // Ignore errors loading sizes
        } finally {
          setIsLoadingSizes(false);
        }
      }
    }

    loadData();
  }, [open, songsetId, renderJobId]);

  const formattedMessage = shareInfo
    ? `I shared a Stream of Worship songset with you:\n\n${songsetName}\nDuration: ${formatShareDuration(durationSeconds)}\n\nOpen this link to view the song list in read-only mode and start Worship Playback:\n${shareInfo.shareUrl}`
    : "";

  const handleCreateShareLink = useCallback(async () => {
    setIsLoadingShare(true);
    try {
      const res = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ songsetId, allowDownload: false }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error ?? "Failed to create share link");
      }

      const data = await res.json();
      setShareInfo({ token: data.token, shareUrl: data.shareUrl });
      toast.success("Share link created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create share link");
    } finally {
      setIsLoadingShare(false);
    }
  }, [songsetId]);

  const handleCopyMessage = useCallback(() => {
    if (!formattedMessage) return;
    navigator.clipboard.writeText(formattedMessage).then(() => {
      toast.success("Share message copied to clipboard");
    });
  }, [formattedMessage]);

  const handleRevoke = useCallback(async () => {
    if (!shareInfo?.token) return;

    setIsRevoking(true);
    try {
      const res = await fetch(`/api/share/${shareInfo.token}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error ?? "Failed to revoke share");
      }
      setShareInfo(null);
      toast.success("Share link revoked");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to revoke share");
    } finally {
      setIsRevoking(false);
    }
  }, [shareInfo]);

  const handleSendFile = useCallback(
    async (app: "whatsapp" | "line" | "email") => {
      if (!shareInfo?.shareUrl) {
        toast.error("Create a share link first");
        return;
      }

      const sharePageUrl = shareInfo.shareUrl;
      const text = `Check out "${songsetName}" on Stream of Worship`;

      if (app === "email") {
        const subject = encodeURIComponent(`Stream of Worship: ${songsetName}`);
        const body = encodeURIComponent(`${text}\n\n${sharePageUrl}`);
        window.open(`mailto:?subject=${subject}&body=${body}`);
      } else if (app === "whatsapp") {
        const msg = encodeURIComponent(`${text}\n${sharePageUrl}`);
        window.open(`https://wa.me/?text=${msg}`);
      } else if (app === "line") {
        const msg = encodeURIComponent(`${text}\n${sharePageUrl}`);
        window.open(`https://line.me/R/msg/text/?${msg}`);
      }
    },
    [shareInfo, songsetName]
  );

  const isAboveLimit = (app: keyof typeof SIZE_LIMITS): boolean => {
    if (!artifactSizes) return false;
    const limit = SIZE_LIMITS[app];
    const mp3 = artifactSizes.mp3SizeBytes;
    const mp4 = artifactSizes.mp4SizeBytes;
    const fileSize = mp4 ?? mp3;
    if (fileSize === null) return false;
    return fileSize > limit;
  };

  const getFileSizeDisplay = (): string => {
    if (!artifactSizes) return "";
    const mp4 = artifactSizes.mp4SizeBytes;
    const mp3 = artifactSizes.mp3SizeBytes;
    const size = mp4 ?? mp3;
    if (size === null) return "";
    return formatBytes(size);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share</DialogTitle>
        </DialogHeader>

        <div className="flex border-b" role="tablist">
          <button
            role="tab"
            aria-selected={activeTab === "share-link"}
            className={cn(
              "flex-1 py-2 text-sm font-medium transition-colors border-b-2 -mb-px",
              activeTab === "share-link"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setActiveTab("share-link")}
          >
            <Link className="size-4 inline mr-1.5 mb-0.5" />
            Share link
          </button>
          {renderJobId && (
            <button
              role="tab"
              aria-selected={activeTab === "send-file"}
              className={cn(
                "flex-1 py-2 text-sm font-medium transition-colors border-b-2 -mb-px",
                activeTab === "send-file"
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setActiveTab("send-file")}
            >
              Send file
            </button>
          )}
        </div>

        {activeTab === "share-link" && (
          <div className="space-y-4 pt-2">
            {isLoadingShare ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : shareInfo ? (
              <>
                <Textarea
                  value={formattedMessage}
                  readOnly
                  className="text-sm min-h-[120px] resize-none"
                  aria-label="Share message"
                />

                <Button
                  variant="outline"
                  className="w-full gap-2"
                  onClick={handleCopyMessage}
                  aria-label="Copy share message"
                >
                  <Copy className="size-4" />
                  Copy message
                </Button>

                <div className="flex items-start gap-2 text-xs text-muted-foreground">
                  <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                  <span>
                    This link stays live. Future edits to this songset will be visible to anyone with the link until you revoke it.
                  </span>
                </div>

                <Button
                  variant="destructive"
                  size="sm"
                  className="w-full gap-2"
                  onClick={handleRevoke}
                  disabled={isRevoking}
                  aria-label="Revoke share link"
                >
                  {isRevoking ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Trash2 className="size-4" />
                  )}
                  Revoke link
                </Button>
              </>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  Create a link to share this songset. Anyone with the link can view the song list and start Worship Playback.
                </p>
                <Button
                  className="w-full gap-2"
                  onClick={handleCreateShareLink}
                  disabled={isLoadingShare}
                  aria-label="Create share link"
                >
                  {isLoadingShare ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Link className="size-4" />
                  )}
                  Create share link
                </Button>
              </>
            )}
          </div>
        )}

        {activeTab === "send-file" && renderJobId && (
          <div className="space-y-4 pt-2">
            {isLoadingSizes ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <>
                {artifactSizes && getFileSizeDisplay() && (
                  <p className="text-xs text-muted-foreground">
                    File size: {getFileSizeDisplay()}
                  </p>
                )}

                <div className="space-y-2">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="block">
                        <Button
                          variant="outline"
                          className="w-full gap-3 justify-start"
                          onClick={() => handleSendFile("whatsapp")}
                          disabled={isAboveLimit("whatsapp")}
                          aria-label="Send via WhatsApp"
                          aria-disabled={isAboveLimit("whatsapp")}
                        >
                          <MessageCircle className="size-5 text-green-500 shrink-0" />
                          <span>WhatsApp</span>
                          <span className="ml-auto text-xs text-muted-foreground">
                            up to {formatLimit(SIZE_LIMITS.whatsapp)}
                          </span>
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {isAboveLimit("whatsapp") && (
                      <TooltipContent>
                        File exceeds WhatsApp&apos;s {formatLimit(SIZE_LIMITS.whatsapp)} limit
                      </TooltipContent>
                    )}
                  </Tooltip>

                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="block">
                        <Button
                          variant="outline"
                          className="w-full gap-3 justify-start"
                          onClick={() => handleSendFile("line")}
                          disabled={isAboveLimit("line")}
                          aria-label="Send via Line"
                          aria-disabled={isAboveLimit("line")}
                        >
                          <MessageCircle className="size-5 text-green-400 shrink-0" />
                          <span>Line</span>
                          <span className="ml-auto text-xs text-muted-foreground">
                            up to {formatLimit(SIZE_LIMITS.line)}
                          </span>
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {isAboveLimit("line") && (
                      <TooltipContent>
                        File exceeds Line&apos;s {formatLimit(SIZE_LIMITS.line)} limit
                      </TooltipContent>
                    )}
                  </Tooltip>

                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="block">
                        <Button
                          variant="outline"
                          className="w-full gap-3 justify-start"
                          onClick={() => handleSendFile("email")}
                          disabled={isAboveLimit("email")}
                          aria-label="Send via Email"
                          aria-disabled={isAboveLimit("email")}
                        >
                          <Mail className="size-5 text-blue-500 shrink-0" />
                          <span>Email</span>
                          <span className="ml-auto text-xs text-muted-foreground">
                            up to {formatLimit(SIZE_LIMITS.email)}
                          </span>
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {isAboveLimit("email") && (
                      <TooltipContent>
                        File exceeds Email&apos;s {formatLimit(SIZE_LIMITS.email)} limit
                      </TooltipContent>
                    )}
                  </Tooltip>
                </div>

                <p className="text-xs text-muted-foreground">
                  Opens your chosen app with a link to the hosted player page.
                </p>
              </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
