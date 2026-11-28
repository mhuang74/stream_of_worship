export function arrangedVideoLeadIn(
  opening: { arrangedOpening?: boolean; openingSilenceSeconds?: number },
  options: { videoEnabled?: boolean; includeTitleCard?: boolean; titleCardDurationSeconds?: number }
): number {
  if (!opening.arrangedOpening || options.videoEnabled === false) return 0;
  const card = options.includeTitleCard
    ? Math.ceil(Math.min(30, Math.max(5, options.titleCardDurationSeconds ?? 5)) * 24) / 24 + 1
    : 0;
  return card + (opening.openingSilenceSeconds ?? 0);
}
