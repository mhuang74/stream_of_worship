import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Shared by LandingPage/AboutPage for conditional button classes; required
// call-site count justifies the exported helper (matches webapp's utils.ts).
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
