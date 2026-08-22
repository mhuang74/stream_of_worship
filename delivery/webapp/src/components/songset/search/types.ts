import type { AlbumFilter } from "@/lib/search/album-filter";
import type { BpmBandKey, SongTheme } from "@/lib/constants";

export type { BpmBandKey } from "@/lib/constants";

export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: BpmBandKey[];
  themes?: SongTheme[];
  albums?: AlbumFilter[];
}
