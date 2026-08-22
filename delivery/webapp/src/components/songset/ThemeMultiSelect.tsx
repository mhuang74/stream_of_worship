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
import { SONG_THEMES, type SongTheme } from "@/lib/constants";
import { useLocale } from "@/hooks/useLocale";

interface ThemeMultiSelectProps {
  selectedThemes: SongTheme[];
  onSelectedThemesChange: (themes: SongTheme[]) => void;
  disabled?: boolean;
  className?: string;
}

export function ThemeMultiSelect({
  selectedThemes,
  onSelectedThemesChange,
  disabled = false,
  className,
}: ThemeMultiSelectProps) {
  const { t } = useLocale();
  const selectedSet = new Set(selectedThemes);

  const toggleTheme = (theme: SongTheme) => {
    if (selectedSet.has(theme)) {
      onSelectedThemesChange(selectedThemes.filter((th) => th !== theme));
    } else {
      onSelectedThemesChange([...selectedThemes, theme]);
    }
  };

  const clearThemes = () => onSelectedThemesChange([]);

  const sortedThemes = [...selectedThemes].sort(
    (a, b) =>
      (SONG_THEMES as readonly string[]).indexOf(a) -
      (SONG_THEMES as readonly string[]).indexOf(b)
  );

  let triggerText: string;
  if (sortedThemes.length === 0) {
    triggerText = t("browse.themes.all");
  } else if (sortedThemes.length === 1) {
    triggerText = t(`theme.${sortedThemes[0]}`);
  } else {
    const labels = sortedThemes.map((theme) => t(`theme.${theme}`));
    if (labels.length === 2) {
      triggerText = labels.join(", ");
    } else {
      triggerText = `${labels.slice(0, 2).join(", ")}, +${labels.length - 2}`;
    }
  }

  return (
    <div className={cn("space-y-2", className)} data-testid="theme-multi-select">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 py-0 text-sm font-medium underline-offset-4"
            disabled={disabled}
            data-testid="theme-filter"
          >
            <span className="max-w-[18rem] truncate whitespace-nowrap">
              <span className="font-medium">{t("browse.themes.label")}</span>{" "}
              <span className="text-muted-foreground">{triggerText}</span>
            </span>
            <ChevronDown className="size-3 text-muted-foreground/60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72 max-h-80">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{t("browse.themes.dropdownLabel")}</DropdownMenuLabel>
            {selectedThemes.length > 0 && (
              <>
                <DropdownMenuItem onClick={clearThemes} data-testid="theme-clear-all">
                  <X className="size-3.5" />
                  {t("browse.themes.clearAll")}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            {SONG_THEMES.map((theme) => (
              <DropdownMenuCheckboxItem
                key={theme}
                checked={selectedSet.has(theme)}
                onCheckedChange={() => toggleTheme(theme)}
                onSelect={(e) => e.preventDefault()}
                data-testid={`theme-option-${encodeURIComponent(theme)}`}
              >
                {t(`theme.${theme}`)}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
