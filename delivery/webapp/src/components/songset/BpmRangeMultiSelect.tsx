"use client";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  BPM_BANDS,
  BPM_BAND_KEYS,
  formatBpmBandRangeText,
  type BpmBandKey,
} from "@/lib/constants";

interface BpmRangeMultiSelectProps {
  selectedBpm: BpmBandKey[];
  onSelectedBpmChange: (bands: BpmBandKey[]) => void;
  disabled?: boolean;
  className?: string;
}

export function BpmRangeMultiSelect({
  selectedBpm,
  onSelectedBpmChange,
  disabled = false,
  className,
}: BpmRangeMultiSelectProps) {
  const selectedSet = new Set(selectedBpm);

  const toggleBpm = (band: BpmBandKey) => {
    if (selectedSet.has(band)) {
      onSelectedBpmChange(selectedBpm.filter((b) => b !== band));
    } else {
      onSelectedBpmChange([...selectedBpm, band]);
    }
  };

  const clearBpm = () => onSelectedBpmChange([]);

  const sortedBpm = [...selectedBpm].sort(
    (a, b) => BPM_BAND_KEYS.indexOf(a) - BPM_BAND_KEYS.indexOf(b)
  );

  let triggerText: string;
  if (sortedBpm.length === 0) {
    triggerText = "All";
  } else {
    triggerText = sortedBpm.map((band) => BPM_BANDS[band].label).join(", ");
  }

  return (
    <div className={cn("space-y-2", className)} data-testid="bpm-range-multi-select">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 py-0 text-sm font-medium underline-offset-4"
            disabled={disabled}
            data-testid="bpm-filter"
          >
            <span className="max-w-[18rem] truncate whitespace-nowrap">
              <span className="font-medium">BPM:</span>{" "}
              <span className="text-muted-foreground">{triggerText}</span>
            </span>
            <ChevronDown className="size-3 text-muted-foreground/60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72 max-h-80">
          <DropdownMenuGroup>
            <DropdownMenuLabel>BPM Range</DropdownMenuLabel>
            {selectedBpm.length > 0 && (
              <>
                <DropdownMenuItem onClick={clearBpm} data-testid="bpm-clear-all">
                  <X className="size-3.5" />
                  Clear all
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            {BPM_BAND_KEYS.map((band) => (
              <DropdownMenuCheckboxItem
                key={band}
                checked={selectedSet.has(band)}
                onCheckedChange={() => toggleBpm(band)}
                onSelect={(e) => e.preventDefault()}
                data-testid={`bpm-option-${band}`}
              >
                {BPM_BANDS[band].label} ({formatBpmBandRangeText(band)})
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
