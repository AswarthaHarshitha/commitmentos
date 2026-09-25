"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { BrandMark } from "@/components/brand";
import { QuickAddProvider } from "@/components/quick-add";
import { MailBanner } from "@/components/shell/mail-banner";
import { BottomNav } from "@/components/shell/bottom-nav";
import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/topbar";
import { ApiError, UNAUTHORIZED_EVENT } from "@/lib/api";
import { ClockProvider } from "@/lib/clock";
import { useMe } from "@/lib/queries";

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: me, error, isPending } = useMe();
  const signedOut = error instanceof ApiError && error.status === 401;

  useEffect(() => {
    const toLogin = () => router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    if (signedOut) toLogin();
    window.addEventListener(UNAUTHORIZED_EVENT, toLogin);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, toLogin);
  }, [signedOut, router, pathname]);

  if (!me) {
    return (
      <div className="grid min-h-dvh place-items-center" role="status" aria-label={isPending ? "Loading your commitments" : "Signing in"}>
        <BrandMark className="size-9 animate-breathe" />
      </div>
    );
  }
  return (
    <ClockProvider>
      <QuickAddProvider>
      <div className="flex min-h-dvh">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1120px] flex-1 px-4 pb-28 pt-6 outline-none md:px-8 md:pb-16 md:pt-8">
            <MailBanner />
            {children}
          </main>
        </div>
      </div>
      <BottomNav />
      </QuickAddProvider>
    </ClockProvider>
  );
}
