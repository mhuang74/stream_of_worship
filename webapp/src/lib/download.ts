export function sanitizeFilename(name: string): string {
  return name
    .trim()
    .replace(/[/\\:*?"<>|#]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
}

export function downloadArtifact(url: string): void {
  window.location.href = url;
}
