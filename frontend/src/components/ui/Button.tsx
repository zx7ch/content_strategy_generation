import type { ButtonHTMLAttributes, PropsWithChildren } from "react";

type ButtonVariant = "primary" | "outline" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "border border-ink bg-ink text-white hover:bg-slate-800",
  outline:
    "border border-line bg-white text-ink hover:bg-slate-50",
  ghost:
    "border border-transparent bg-slate-100 text-ink hover:bg-slate-200"
};

export function Button({
  children,
  className = "",
  type = "button",
  variant = "outline",
  ...props
}: PropsWithChildren<ButtonProps>) {
  return (
    <button
      type={type}
      className={[
        "inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-full px-4 py-2 text-sm font-medium transition",
        variantClasses[variant],
        className
      ].join(" ")}
      {...props}
    >
      {children}
    </button>
  );
}
