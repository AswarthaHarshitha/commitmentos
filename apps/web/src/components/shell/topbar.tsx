"use client";

import { FilePlus2, Mail, Plus } from "lucide-react";
import Link from "next/link";
import { Brand } from "@/components/brand";
import { useQuickAdd } from "@/components/quick-add";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { AccountMenu } from "./account-menu";
import { NotificationsBell } from "./notifications";

export function TopBar() {
  const { addCommitment, importEmail } = useQuickAdd();
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-line bg-bg px-4 md:px-8">
      <Link href="/overview" aria-label="CommitmentOS overview" className="md:hidden">
        <Brand />
      </Link>
      <div className="hidden md:block" />
      <div className="flex items-center gap-1.5">
        <DropdownMenu
          label="Add"
          trigger={
            <Button size="sm" aria-label="Add" icon={<Plus aria-hidden className="size-4" strokeWidth={1.9} />}>
              <span className="hidden sm:inline">Add</span>
            </Button>
          }
          actions={[
            { label: "Import an email", icon: Mail, onSelect: importEmail },
            { label: "Add a commitment", icon: FilePlus2, onSelect: addCommitment },
          ]}
        />
        <NotificationsBell />
        <div className="md:hidden">
          <AccountMenu compact />
        </div>
      </div>
    </header>
  );
}
