import { Activity, CalendarRange, CircleCheck, Clock, Inbox, LayoutDashboard, Settings, TriangleAlert, Workflow, type LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** which live count to show beside the label */
  badge?: "inbox" | "overdue";
}

export const PRIMARY_NAV: NavItem[] = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/inbox", label: "Inbox", icon: Inbox, badge: "inbox" },
  { href: "/today", label: "Today", icon: Clock },
  { href: "/upcoming", label: "Upcoming", icon: CalendarRange },
  { href: "/overdue", label: "Overdue", icon: TriangleAlert, badge: "overdue" },
  { href: "/completed", label: "Completed", icon: CircleCheck },
];

export const SYSTEM_NAV: NavItem[] = [
  { href: "/automations", label: "Automations", icon: Workflow },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];
