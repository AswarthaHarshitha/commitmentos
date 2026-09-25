"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { AuthCard } from "@/components/auth-form";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { errorMessage } from "@/lib/api";
import { useAuth, useMe } from "@/lib/queries";

const MIN_PASSWORD = 10;
const noSubscription = () => () => {};
// the server has no idea what zone the visitor is in; React swaps in the browser's value after hydration without a mismatch
const detectedZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export default function RegisterPage() {
  const router = useRouter();
  const { data: me } = useMe();
  const { register } = useAuth();
  const zone = useSyncExternalStore(noSubscription, detectedZone, () => "UTC");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (me) router.replace("/overview");
  }, [me, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password.length < MIN_PASSWORD) {
      setError(`Choose a password of at least ${MIN_PASSWORD} characters.`);
      return;
    }
    try {
      await register.mutateAsync({ email: email.trim(), password, display_name: name.trim(), timezone: zone });
      router.replace("/overview");
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <AuthCard
      title="Create your account"
      description="Start with your own commitments. It takes a minute."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Your name" hint="Used to greet you.">
          {(p) => <Input {...p} autoComplete="name" autoFocus maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Password" hint={`At least ${MIN_PASSWORD} characters.`}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" required minLength={MIN_PASSWORD} value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <p className="text-[13px] text-text-2">
          Your time zone is set to <span className="font-medium text-ink">{zone.replace(/_/g, " ")}</span>. You can change it later in Settings.
        </p>
        {error && (
          <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[13.5px] text-danger">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full" loading={register.isPending} disabled={!email.trim() || !password}>
          Create account
        </Button>
      </form>
    </AuthCard>
  );
}
