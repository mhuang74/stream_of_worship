import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { verifyValidationToken } from "@/lib/lead-validation";
import { markValidated } from "@/lib/brevo/client";
import { getMarketingUrl } from "@/lib/marketing-url";
import { resolveUserLocale } from "@/lib/i18n/server";
import { t, type TranslationKey } from "@/lib/i18n/messages";

// NOTE: server component — this page verifies the token, performs the Brevo
// VALIDATED write during render (idempotent; email-scanner prefetch on GET
// validation links is accepted), and renders the confirmation UI.
//
// Public: listed in PUBLIC_PATHS (src/proxy.ts) so unauthenticated lead
// visitors reach it. No rate limiter: tokens are HMAC-unforgeable and expire
// in 48h, so there is nothing to brute-force.
export default async function ValidatedPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  const locale = await resolveUserLocale();
  const verified = token ? verifyValidationToken(token) : null;

  if (verified) {
    await markValidated(verified.email);
  }

  const tt = (key: TranslationKey): string => t(locale, key);

  if (!verified) {
    return (
      <Shell>
        <Card className="w-full max-w-sm">
          <CardHeader className="space-y-1">
            <CardTitle className="text-2xl">{tt("lead.validated.invalid.title")}</CardTitle>
            <CardDescription>{tt("lead.validated.invalid.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Link href="/register" className={cn(buttonVariants(), "w-full")}>
              {tt("lead.validated.ctaRegister")}
            </Link>
            <a
              href={getMarketingUrl(locale)}
              className={cn(buttonVariants({ variant: "outline" }), "w-full")}
            >
              {tt("lead.validated.ctaMarketing")}
            </a>
          </CardContent>
        </Card>
      </Shell>
    );
  }

  const registerUrl = `/register?email=${encodeURIComponent(verified.email)}`;

  return (
    <Shell>
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1">
          <CardTitle className="text-2xl">{tt("lead.validated.success.title")}</CardTitle>
          <CardDescription>{tt("lead.validated.success.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Link href={registerUrl} className={cn(buttonVariants(), "w-full")}>
            {tt("lead.validated.ctaRegister")}
          </Link>
          <a
            href={getMarketingUrl(locale)}
            className={cn(buttonVariants({ variant: "outline" }), "w-full")}
          >
            {tt("lead.validated.ctaMarketing")}
          </a>
        </CardContent>
      </Card>
    </Shell>
  );
}

// Same layout shell as the register card page (single-column centered card).
function Shell({ children }: { children: React.ReactNode }) {
  return <div className="min-h-screen flex items-center justify-center p-4">{children}</div>;
}
