/**
 * Thin, styled wrappers over the four Radix primitives the viewer uses: Tabs, Tooltip, Dialog
 * and Popover. Radix supplies focus management and ARIA; these supply the look.
 */
import * as RadixDialog from "@radix-ui/react-dialog";
import * as RadixPopover from "@radix-ui/react-popover";
import * as RadixTabs from "@radix-ui/react-tabs";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { type ReactElement, type ReactNode, useRef } from "react";
import { IconButton } from "./Button";
import { cx } from "./tone";

const floating =
  "z-50 rounded-card border border-line bg-surface text-fg shadow-float animate-fade-in";

// ---------- Tabs ----------

export type TabItem = { id: string; label: string; content: ReactNode };

/**
 * A tab strip with its panels. Uncontrolled unless `value` is given. Arrow keys move between
 * tabs (Radix), and each panel is focusable so keyboard users can scroll it.
 *
 * The strip scrolls sideways when the tabs do not fit, which makes it a scroll container in
 * both directions. So nothing in it may stick out vertically, not even by the pixel a tab's
 * underline would need to cover a border: the hairline under the strip is a shadow inside the
 * strip's own box, and each tab draws its underline over it.
 */
export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  label,
  className,
}: {
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (id: string) => void;
  /** Names the tab list for assistive tech, e.g. "Span details". */
  label: string;
  className?: string;
}) {
  return (
    <RadixTabs.Root
      value={value}
      defaultValue={defaultValue ?? items[0]?.id}
      onValueChange={onValueChange}
      className={cx("flex min-h-0 flex-col", className)}
    >
      <RadixTabs.List
        aria-label={label}
        className="flex shrink-0 gap-1 overflow-x-auto px-1.5 shadow-[inset_0_-1px_0_var(--border)]"
      >
        {items.map((item) => (
          <RadixTabs.Trigger
            key={item.id}
            value={item.id}
            className={cx(
              // The padding is room for the focus ring, which is drawn inside the tab: outside
              // it, the strip would clip it.
              "h-8 shrink-0 border-transparent border-b-2 px-1.5 text-muted text-ui",
              "transition-colors duration-150 ease-out hover:text-fg focus-visible:outline-offset-[-2px]",
              "data-[state=active]:border-accent data-[state=active]:font-medium data-[state=active]:text-fg",
            )}
          >
            {item.label}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {items.map((item) => (
        <RadixTabs.Content
          key={item.id}
          value={item.id}
          className="min-h-0 flex-1 overflow-auto p-3 focus-visible:outline-offset-[-2px]"
        >
          {item.content}
        </RadixTabs.Content>
      ))}
    </RadixTabs.Root>
  );
}

// ---------- Tooltip ----------

/** Wrap the app once so tooltips share timing. */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <RadixTooltip.Provider delayDuration={350} skipDelayDuration={200}>
      {children}
    </RadixTooltip.Provider>
  );
}

/**
 * A hint on hover and focus. Never the only place a fact lives: the trigger must already have
 * an accessible name, and anything essential must be visible elsewhere.
 */
export function Tooltip({
  content,
  side = "bottom",
  children,
}: {
  content: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  children: ReactElement;
}) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cx(floating, "max-w-64 rounded-control px-2 py-1 text-data text-muted")}
        >
          {content}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}

// ---------- Dialog ----------

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  // Radix returns the focus to its own trigger. This dialog has none: it is opened by a button
  // elsewhere or by a key, so what had the focus is remembered here and gets it back.
  const opener = useRef<HTMLElement | null>(null);
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-scrim" />
        <RadixDialog.Content
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={() => {
            const active = document.activeElement;
            opener.current = active instanceof HTMLElement ? active : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            opener.current?.focus();
          }}
          className={cx(
            floating,
            "-translate-x-1/2 fixed top-[14vh] left-1/2 flex max-h-[72vh] w-[min(520px,calc(100vw-32px))] flex-col",
          )}
        >
          <header className="flex items-start justify-between gap-3 border-line border-b px-4 py-3">
            <div className="min-w-0">
              <RadixDialog.Title className="m-0 font-semibold text-title">
                {title}
              </RadixDialog.Title>
              {description && (
                <RadixDialog.Description className="m-0 mt-0.5 text-muted text-ui">
                  {description}
                </RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <IconButton icon="close" label="Close" className="-mt-0.5 -mr-1.5" />
            </RadixDialog.Close>
          </header>
          <div className="min-h-0 overflow-auto p-4">{children}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

// ---------- Popover ----------

export function Popover({
  trigger,
  align = "end",
  label,
  children,
}: {
  trigger: ReactElement;
  align?: "start" | "center" | "end";
  /** Names the popover's content for assistive tech. */
  label: string;
  children: ReactNode;
}) {
  return (
    <RadixPopover.Root>
      <RadixPopover.Trigger asChild>{trigger}</RadixPopover.Trigger>
      <RadixPopover.Portal>
        <RadixPopover.Content
          align={align}
          sideOffset={6}
          collisionPadding={8}
          aria-label={label}
          className={cx(floating, "w-72 p-3")}
        >
          {children}
        </RadixPopover.Content>
      </RadixPopover.Portal>
    </RadixPopover.Root>
  );
}
