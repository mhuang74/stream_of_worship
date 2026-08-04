export function formatTimestamp(timeSeconds: number): string {
  const minutes = Math.floor(timeSeconds / 60);
  const seconds = Math.floor(timeSeconds % 60);
  const hundredths = Math.floor((timeSeconds % 1) * 100);
  return `[${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${hundredths.toString().padStart(2, "0")}]`;
}
