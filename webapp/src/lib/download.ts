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
  const link = document.createElement("a");
  link.href = url;
  link.style.display = "none";
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    document.body.removeChild(link);
  }
}

export async function fetchSignedUrlAndDownload(
  renderJobId: string,
  fileType: "audio" | "video" | "json",
  filename: string,
  extension: string,
): Promise<void> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30_000);

  const disposition = `attachment; filename="${filename}.${extension}"`;
  const res = await fetch(
    `/api/signed-url?renderJobId=${encodeURIComponent(renderJobId)}` +
      `&fileType=${fileType}` +
      `&contentDisposition=${encodeURIComponent(disposition)}`,
    { signal: controller.signal }
  );
  clearTimeout(timeoutId);

  if (!res.ok) {
    let message = "Failed to get download URL";
    try {
      const body = await res.json();
      if (body.error) message = body.error;
    } catch { /* keep default message */ }
    throw new Error(message);
  }
  const { url } = await res.json();
  downloadArtifact(url);
}

export function downloadArtifactViaProxy(
  renderJobId: string,
  fileType: "audio" | "video" | "json",
): void {
  const artifactFile =
    fileType === "audio" ? "output.mp3" :
    fileType === "video" ? "output.mp4" : "chapters.json";

  const proxyUrl = `/api/r2/artifact/${renderJobId}/${artifactFile}?download=1`;
  downloadArtifact(proxyUrl);
}
