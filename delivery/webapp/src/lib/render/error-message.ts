const MAX_LENGTH = 250;

const ANSI_ESCAPE = /\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[=>]/g;
const CONTROL_CHARS = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g;
const URL_PATTERN = /\bhttps?:\/\/[^\s]+/gi;
const UNIX_PATH = /(^|[\s(:'\[])(\/(?:[^/\s]+\/)+[^/\s]+)/g;
const WINDOWS_PATH = /\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+/g;
const SECRET_PATTERN = /\b(TOKEN|API_KEY|PASSWORD|SECRET|DATABASE_URL|SOW_\w*_KEY)=[^\s]*/gi;

const TRACEBACK_PREFIXES = [
  /^\s*Traceback \(most recent call last\):\s*/i,
  /^\s*File "[^"]*", line \d+, in .*\s*/i,
  /^\s*at\s+\S+\s+\([^)]*\)\s*$/i,
  /^\s*at\s+\S+\s*$/i,
  /^\s*Caused by:.*$/i,
  /^\s*During handling of.*$/i,
  /^\s*The above exception.*$/i,
];

export function sanitizeRenderErrorMessage(message: unknown): string | null {
  if (typeof message !== "string") return null;

  let text = message.replace(ANSI_ESCAPE, "").replace(CONTROL_CHARS, "");

  const lines = text.split(/\r?\n/);
  let firstUseful: string | null = null;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (TRACEBACK_PREFIXES.some((re) => re.test(trimmed))) continue;
    firstUseful = trimmed;
    break;
  }

  if (firstUseful === null) return null;

  text = firstUseful;

  text = text.replace(SECRET_PATTERN, "$1=[redacted]");
  text = text.replace(URL_PATTERN, "[url]");
  text = text.replace(UNIX_PATH, "$1[path]");
  text = text.replace(WINDOWS_PATH, "[path]");

  text = text.replace(/\s+/g, " ").trim();

  if (!text) return null;

  const PLACEHOLDER_ONLY = /^((\w+=)?\[url\]|(\w+=)?\[path\]|(\w+=)?\[redacted\]|\s)+$/;
  if (PLACEHOLDER_ONLY.test(text)) return null;

  if (text.length > MAX_LENGTH) {
    text = text.slice(0, MAX_LENGTH - 1) + "…";
  }

  return text;
}

export function formatRenderFailedAt(date: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

export function getRenderFailureText(
  errorMessage: string | null | undefined,
  failedAt: Date | null | undefined
): string {
  const sanitized = sanitizeRenderErrorMessage(errorMessage);
  if (sanitized) return sanitized;
  if (failedAt) {
    return `Render failed around ${formatRenderFailedAt(failedAt)}. Please render again.`;
  }
  return "Render failed. Please render again.";
}
