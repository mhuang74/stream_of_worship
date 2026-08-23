"use client";

import { useEffect, useRef, useState } from "react";
import { updateUser, useSession } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLocale } from "@/hooks/useLocale";

interface NameErrors {
  name?: string;
  form?: string;
}

// Display-name form of the Account settings section (spec v1, Phase 7). Has
// its own submit, validation, and error handling.
export function NameForm() {
  const { t } = useLocale();
  const { data: session } = useSession();
  const user = session?.user;

  const [name, setName] = useState("");
  // useSession() returns { data: null } on the first render while the session
  // loads, so the field cannot be initialized from user.name in useState.
  // Sync once when the session arrives; never clobber edits afterwards.
  const nameInitialized = useRef(false);
  useEffect(() => {
    if (!nameInitialized.current && user?.name != null) {
      setName(user.name);
      nameInitialized.current = true;
    }
  }, [user?.name]);
  const [nameErrors, setNameErrors] = useState<NameErrors>({});
  const [savingName, setSavingName] = useState(false);
  const [nameSaved, setNameSaved] = useState(false);

  async function handleNameSubmit(e: React.FormEvent) {
    e.preventDefault();
    const next: NameErrors = {};
    if (!name.trim()) {
      next.name = t("settings.account.nameRequired");
    }
    if (Object.keys(next).length > 0) {
      setNameErrors(next);
      return;
    }
    setNameErrors({});
    setSavingName(true);
    setNameSaved(false);
    try {
      const result = await updateUser({ name });
      if (result.error) {
        setNameErrors({ form: result.error.message ?? t("settings.account.nameFailed") });
      } else {
        setNameSaved(true);
      }
    } catch {
      setNameErrors({ form: t("settings.account.nameFailed") });
    } finally {
      setSavingName(false);
    }
  }

  return (
    <form onSubmit={handleNameSubmit} noValidate className="space-y-2">
      <Label htmlFor="account-name">{t("settings.account.name")}</Label>
      <Input
        id="account-name"
        type="text"
        autoComplete="name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        aria-describedby={nameErrors.name ? "account-name-error" : undefined}
        aria-invalid={!!nameErrors.name}
      />
      {nameErrors.name && (
        <p id="account-name-error" className="text-sm text-destructive" role="alert">
          {nameErrors.name}
        </p>
      )}
      {nameErrors.form && (
        <p className="text-sm text-destructive" role="alert">
          {nameErrors.form}
        </p>
      )}
      {nameSaved && (
        <p className="text-sm text-muted-foreground" role="status">
          {t("settings.account.nameSaved")}
        </p>
      )}
      <Button type="submit" disabled={savingName}>
        {savingName ? t("settings.account.saving") : t("settings.account.saveName")}
      </Button>
    </form>
  );
}
