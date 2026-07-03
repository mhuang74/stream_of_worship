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
import { PITCH_CLASSES } from "@/lib/constants";

interface MusicalKeyMultiSelectProps {
  selectedKeys: string[];
  onSelectedKeysChange: (keys: string[]) => void;
  disabled?: boolean;
  className?: string;
}

export function MusicalKeyMultiSelect({
  selectedKeys,
  onSelectedKeysChange,
  disabled = false,
  className,
}: MusicalKeyMultiSelectProps) {
  const selectedSet = new Set(selectedKeys);

  const toggleKey = (key: string) => {
    if (selectedSet.has(key)) {
      onSelectedKeysChange(selectedKeys.filter((k) => k !== key));
    } else {
      onSelectedKeysChange([...selectedKeys, key]);
    }
  };

  const clearKeys = () => onSelectedKeysChange([]);

  let triggerText: string;
  if (selectedKeys.length === 0) {
    triggerText = "All Musical Keys";
  } else if (selectedKeys.length === 1) {
    triggerText = selectedKeys[0];
  } else if (selectedKeys.length === 2) {
    triggerText = selectedKeys.join(", ");
  } else {
    triggerText = `${selectedKeys.slice(0, 2).join(", ")}, +${selectedKeys.length - 2}`;
  }

  return (
    <div className={cn("space-y-2", className)} data-testid="musical-key-multi-select">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 py-0 text-sm font-medium underline-offset-4"
            disabled={disabled}
            data-testid="key-filter"
          >
            <span className="max-w-[18rem] truncate">{triggerText}</span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72 max-h-80">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Musical Key</DropdownMenuLabel>
            {selectedKeys.length > 0 && (
              <>
                <DropdownMenuItem onClick={clearKeys} data-testid="key-clear-all">
                  <X className="size-3.5" />
                  Clear all
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            {PITCH_CLASSES.map((key) => (
              <DropdownMenuCheckboxItem
                key={key}
                checked={selectedSet.has(key)}
                onCheckedChange={() => toggleKey(key)}
                data-testid={`key-option-${key.replace("#", "sharp")}`}
              >
                {key}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
