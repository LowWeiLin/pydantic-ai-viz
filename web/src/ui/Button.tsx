import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { Icon, type IconName } from "./Icon";
import { cx } from "./tone";

type Variant = "primary" | "secondary" | "ghost";

const base =
  "inline-flex items-center justify-center gap-1.5 rounded-control border font-medium " +
  "select-none whitespace-nowrap transition-[background-color,border-color,color,transform] " +
  "duration-150 ease-out active:translate-y-px disabled:opacity-50 disabled:active:translate-y-0";

const variants: Record<Variant, string> = {
  primary: "border-transparent bg-accent text-accent-fg hover:brightness-110",
  secondary: "border-line-strong bg-surface text-fg hover:bg-surface-2",
  ghost: "border-transparent text-muted hover:bg-surface-2 hover:text-fg",
};

const sizes = { sm: "h-6 px-2 text-data", md: "h-7 px-2.5 text-ui" };

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: keyof typeof sizes;
  icon?: IconName;
  ref?: Ref<HTMLButtonElement>;
};

export function Button({
  variant = "secondary",
  size = "md",
  icon,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button type={type} className={cx(base, variants[variant], sizes[size], className)} {...rest}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: IconName;
  /** Required: an icon-only control needs a name. */
  label: string;
  /** Toggle state, for buttons that switch something on and off. */
  pressed?: boolean;
  ref?: Ref<HTMLButtonElement>;
};

export function IconButton({
  icon,
  label,
  pressed,
  className,
  type = "button",
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      className={cx(base, variants.ghost, "size-7", pressed && "bg-surface-2 text-fg", className)}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}

/** A link styled as a button, for navigation and downloads. */
export function ButtonLink({
  variant = "secondary",
  size = "md",
  icon,
  className,
  children,
  ...rest
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: Variant;
  size?: keyof typeof sizes;
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <a className={cx(base, variants[variant], sizes[size], "no-underline", className)} {...rest}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </a>
  );
}
