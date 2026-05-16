"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, Save, RotateCcw } from "lucide-react";
import { toast } from "sonner";

interface LyricsEditorProps {
  recordingContentHash: string;
  lrcContent: string;
  onSave: (newLrc: string) => void;
}

export function LyricsEditor({ recordingContentHash, lrcContent, onSave }: LyricsEditorProps) {
  const [editedLrc, setEditedLrc] = useState(lrcContent);
  const [isSaving, setIsSaving] = useState(false);

  const isDirty = editedLrc !== lrcContent;

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch("/api/lyrics/overrides", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recordingContentHash, lrcContent: editedLrc }),
      });
      if (!res.ok) throw new Error("Failed to save");
      onSave(editedLrc);
      toast.success("Lyrics saved");
    } catch {
      toast.error("Failed to save lyrics");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Edit the raw LRC file content</p>
        <div className="flex gap-2">
          {isDirty && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditedLrc(lrcContent)}
              aria-label="Reset to original"
            >
              <RotateCcw className="size-4" />
              Reset
            </Button>
          )}
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!isDirty || isSaving}
            aria-label="Save lyrics"
          >
            {isSaving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save
          </Button>
        </div>
      </div>
      <Textarea
        value={editedLrc}
        onChange={(e) => setEditedLrc(e.target.value)}
        className="font-mono text-xs min-h-[400px] resize-y"
        spellCheck={false}
        aria-label="LRC content editor"
      />
    </div>
  );
}
