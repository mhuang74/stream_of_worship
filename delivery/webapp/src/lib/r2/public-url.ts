const R2_PUBLIC_DOMAIN = process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN?.replace(/^https?:\/\//, "");

export function getPublicAudioUrl(hashPrefix: string): string | null {
  if (!R2_PUBLIC_DOMAIN) return null;
  return `https://${R2_PUBLIC_DOMAIN}/${hashPrefix}/audio.mp3`;
}
