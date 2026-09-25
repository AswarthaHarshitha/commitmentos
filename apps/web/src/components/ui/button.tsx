import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "quiet-danger";
type Size = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

const variants: Record<Variant, string> = {
  primary: "bg-ink text-surface hover:bg-[#3a362f] border border-transparent",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-surface-2",
  ghost: "bg-transparent text-text-2 hover:bg-surface-2 hover:text-ink border border-transparent",
  "quiet-danger": "bg-transparent text-danger hover:bg-danger-bg border border-transparent",
};

const sizes: Record<Size, string> = { sm: "h-8 px-3 text-[13px] gap-1.5", md: "h-9 px-3.5 text-[14px] gap-2" };

function Spinner() {
  return <span aria-hidden className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70" />;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, icon, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-md font-medium transition-[background-color,color,transform] duration-150 active:translate-y-px",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0",
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
});
