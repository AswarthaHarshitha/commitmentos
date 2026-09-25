"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth-form";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { errorMessage } from "@/lib/api";
import { useAuth, useMe } from "@/lib/queries";
import { safeNext } from "@/lib/redirect";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const destination = safeNext(params.get("next"));
  const { data: me } = useMe();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  // someone who is already signed in has no use for this page
  useEffect(() => {
    if (me) router.replace(destination);
  }, [me, router, destination]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email: email.trim(), password });
      router.replace(destination);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <AuthCard
      title="Welcome back"
      description="Sign in to see what is due."
      footer={
        <>
          New here?{" "}
          <Link href="/register" className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Password">{(p) => <Input {...p} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
        {error && (
          <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[13.5px] text-danger">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full" loading={login.isPending} disabled={!email.trim() || !password}>
          Sign in
        </Button>
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
