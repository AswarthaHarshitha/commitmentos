import Link from "next/link";
import type { ReactNode } from "react";
import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-[1120px] items-center justify-between px-4 py-5 md:px-8">
        <Link href="/" aria-label="CommitmentOS home">
          <Brand />
        </Link>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-4 pb-20 outline-none">
        {children}
      </main>
    </div>
  );
}
