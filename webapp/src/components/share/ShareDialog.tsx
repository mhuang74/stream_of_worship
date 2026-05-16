"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "sonner";
import { Copy, Trash2, Link, MessageCircle, Mail, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// File size limits per platform (in bytes)
const SIZE_LIMITS = {
  whatsapp: 2 * 1024 * 1024 * 1024, // 2 GB
  line: 1 * 1024 * 1024 * 1024, // 1 GB
  email: 25 * 1024 * 1024, // 25 MB
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

export interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  renderJobId: string;
  songsetName: string;
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
  renderJobId,
  songsetName,
}: ShareDialogProps) {
  const [activeTab, setActiveTab] = useState<Tab>("share-link");
  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);
  const [artifactSizes, setArtifactSizes] = useState<ArtifactSizes | null>(null);
  const [isLoadingShare, setIsLoadingShare] = useState(false);
  const [isLoadingSizes, setIsLoadingSizes] = useState(false);
  const [isRevoking, setIsRevoking] = useState(false);

  // Load existing share info and artifact sizes when dialog opens
  useEffect(() => {
    if (!open) return;

    async function loadData() {
      // Load existing share tokens for this render job
      setIsLoadingShare(true);
      try {
        const res = await fetch(`/api/share?renderJobId=${encodeURIComponent(renderJobId)}`);
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

      // Load artifact sizes for Send File tab
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

    loadData();
  }, [open, renderJobId]);

  const handleCreateShareLink = useCallback(async () => {
    setIsLoadingShare(true);
    try {
      const res = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ renderJobId, allowDownload: false }),
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
  }, [renderJobId]);

  const handleCopyLink = useCallback(() => {
    if (!shareInfo?.shareUrl) return;
    navigator.clipboard.writeText(shareInfo.shareUrl).then(() => {
      toast.success("Link copied to clipboard");
    });
  }, [shareInfo]);

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

        {/* Tab switcher */}
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
        </div>

        {/* Share Link tab */}
        {activeTab === "share-link" && (
          <div className="space-y-4 pt-2">
            {isLoadingShare ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : shareInfo ? (
              <>
                <div className="flex gap-2">
                  <Input
                    value={shareInfo.shareUrl}
                    readOnly
                    className="text-sm font-mono"
                    aria-label="Share link URL"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={handleCopyLink}
                    aria-label="Copy share link"
                  >
                    <Copy className="size-4" />
                  </Button>
                </div>

                <p className="text-xs text-muted-foreground">
                  Anyone with this link can stream the worship video. Revoking stops streams; downloaded files unaffected.
                </p>

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
                  Create a link to share this worship video publicly. Anyone with the link can stream it.
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

        {/* Send File tab */}
        {activeTab === "send-file" && (
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
                  {/* WhatsApp */}
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

                  {/* Line */}
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

                  {/* Email */}
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
