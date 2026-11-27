// One sentence for a stale AI transition, shared by the editor rail and the
// render preflight so the two surfaces cannot drift.

import type { TranslationKey } from "@/lib/i18n/messages";
import type { GlueStaleChangeDetail } from "@/lib/glue/stale";

function fill(template: string, values: Record<string, string>): string {
  let out = template;
  for (const [token, value] of Object.entries(values)) {
    out = out.replaceAll(`{${token}}`, value);
  }
  return out;
}

/**
 * Names the change that made the stored take stale. `titles` are the outgoing
 * and incoming Song titles; only the `{song}` placeholders that matter to the
 * change are substituted.
 */
export function glueStaleMessage(
  detail: GlueStaleChangeDetail | null,
  t: (key: TranslationKey) => string,
  titles: { from: string; to: string }
): string {
  if (detail === null) return t("glue.reason.stale");
  switch (detail.change) {
    case "from-recording":
      return fill(t("glue.stale.recording"), { song: titles.from });
    case "to-recording":
      return fill(t("glue.stale.recording"), { song: titles.to });
    case "to-arrangement":
      return fill(t("glue.stale.arrangement"), { song: titles.to });
    case "from-arrangement":
      return fill(t("glue.stale.arrangement"), { song: titles.from });
    case "incoming-tempo":
      return fill(t("glue.stale.tempo"), { song: titles.to });
    case "beats":
      return fill(t("glue.stale.beats"), {
        from: String(detail.storedBeats ?? ""),
        to: String(detail.currentBeats ?? ""),
      });
  }
}
