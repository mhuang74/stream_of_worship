import type { AlbumFilter } from "@/lib/search/album-filter";
import type { BpmBandKey } from "@/lib/constants";

export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: BpmBandKey[];
  albums?: AlbumFilter[];
}
