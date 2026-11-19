/**
 * The icon set: 16px, 1.5px strokes, drawn for this tool so nothing is fetched or bundled
 * from an icon library. Icons are decorative by default; pass `label` when one stands alone.
 */
import type { ReactNode } from "react";

const paths = {
  // kinds
  agent: (
    <>
      <rect x="3" y="5.5" width="10" height="7.5" rx="2" />
      <path d="M8 3v2.5M6.25 9v1M9.75 9v1" />
      <circle cx="8" cy="2.5" r="0.6" fill="currentColor" />
    </>
  ),
  model: <path d="M8 2.2l1.5 4.3L13.8 8l-4.3 1.5L8 13.8 6.5 9.5 2.2 8l4.3-1.5z" />,
  tool: (
    <path d="M10.6 2.6a3 3 0 0 0-3 3.9l-5 5 1.9 1.9 5-5a3 3 0 0 0 3.9-3l-1.8 1.7-1.6-.4-.4-1.6z" />
  ),
  output: <path d="M2.5 8h7.5M7.5 5l3 3-3 3M13.5 3v10" />,
  graph: (
    <>
      <circle cx="3.5" cy="8" r="1.6" />
      <circle cx="12.5" cy="3.8" r="1.6" />
      <circle cx="12.5" cy="12.2" r="1.6" />
      <path d="M5 7.3l6-2.8M5 8.7l6 2.8" />
    </>
  ),
  span: <path d="M2.5 4.5h7M5.5 8h8M4 11.5h5" />,
  // status
  check: <path d="M3.5 8.5l3 3 6-7" />,
  retry: <path d="M12.6 8a4.6 4.6 0 1 1-1.5-3.4M12.8 2.6v2.6h-2.6" />,
  error: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M6 6l4 4M10 6l-4 4" />
    </>
  ),
  running: <path d="M8 2.4A5.6 5.6 0 1 0 13.6 8" />,
  idle: <circle cx="8" cy="8" r="5.2" strokeDasharray="2 2.4" />,
  warning: <path d="M8 2.5l6 10.5H2zM8 6.5v3M8 11.2v.3" />,
  info: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 7.2v3.6M8 5v.3" />
    </>
  ),
  // chrome
  sun: (
    <>
      <circle cx="8" cy="8" r="2.6" />
      <path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1" />
    </>
  ),
  moon: <path d="M13 9.4A5.4 5.4 0 0 1 6.6 3 5.4 5.4 0 1 0 13 9.4z" />,
  system: (
    <>
      <rect x="2" y="3" width="12" height="8" rx="1.5" />
      <path d="M6 13.5h4M8 11v2.5" />
    </>
  ),
  keyboard: (
    <>
      <rect x="1.8" y="4" width="12.4" height="8" rx="1.5" />
      <path d="M4.5 6.8h.2M7 6.8h.2M9.5 6.8h.2M11.5 6.8h.2M5 9.4h6" />
    </>
  ),
  chevronRight: <path d="M6 3.5L10.5 8 6 12.5" />,
  chevronDown: <path d="M3.5 6L8 10.5 12.5 6" />,
  copy: (
    <>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
    </>
  ),
  upload: <path d="M8 10.5v-8M4.8 5.5L8 2.3l3.2 3.2M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2" />,
  download: (
    <path d="M8 2.5v8M4.8 7.5L8 10.7l3.2-3.2M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2" />
  ),
  play: <path d="M4.5 2.8v10.4L13 8z" />,
  pause: <path d="M5 3v10M11 3v10" />,
  skipEnd: <path d="M3.5 3v10L11 8zM12.5 3v10" />,
  close: <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />,
  live: (
    <>
      <circle cx="8" cy="8" r="1.6" fill="currentColor" />
      <path d="M4.6 11.4a4.8 4.8 0 0 1 0-6.8M11.4 4.6a4.8 4.8 0 0 1 0 6.8" />
    </>
  ),
  file: (
    <>
      <path d="M4 1.8h5l3 3v8.4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.8a1 1 0 0 1 1-1z" />
      <path d="M9 1.8v3h3" />
    </>
  ),
  gallery: (
    <>
      <rect x="2" y="2" width="5" height="5" rx="1" />
      <rect x="9" y="2" width="5" height="5" rx="1" />
      <rect x="2" y="9" width="5" height="5" rx="1" />
      <rect x="9" y="9" width="5" height="5" rx="1" />
    </>
  ),
  eyeOff: (
    <path d="M2 2l12 12M6.4 6.5a2.2 2.2 0 0 0 3.1 3.1M4.3 4.5C2.9 5.4 1.9 6.7 1.5 8c.9 2.6 3.5 4.5 6.5 4.5 1 0 2-.2 2.9-.6M7 3.6c.3 0 .7-.1 1-.1 3 0 5.6 1.9 6.5 4.5-.3.8-.7 1.5-1.3 2.2" />
  ),
  panelRight: (
    <>
      <rect x="2" y="2.5" width="12" height="11" rx="1.5" />
      <path d="M10 2.5v11" />
    </>
  ),
  reload: <path d="M13 8a5 5 0 1 1-1.6-3.7M13.2 2.3v2.8h-2.8" />,
  search: (
    <>
      <circle cx="7" cy="7" r="4.2" />
      <path d="M10.2 10.2l3.3 3.3" />
    </>
  ),
  // where a blueprint fact comes from
  blueprint: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M4.8 6h2.6v2.6H4.8zM7.4 7.3h3.8v2.9" />
    </>
  ),
  code: <path d="M5.2 4.6L1.8 8l3.4 3.4M10.8 4.6L14.2 8l-3.4 3.4M9.2 3.2l-2.4 9.6" />,
  object: <path d="M8 1.8l5.5 3v6.4L8 14.2l-5.5-3V4.8zM2.5 4.8L8 7.8l5.5-3M8 7.8v6.4" />,
  eye: (
    <>
      <path d="M1.5 8c.9-2.6 3.5-4.5 6.5-4.5s5.6 1.9 6.5 4.5c-.9 2.6-3.5 4.5-6.5 4.5S2.4 10.6 1.5 8z" />
      <circle cx="8" cy="8" r="2" />
    </>
  ),
  // canvas and playback
  menu: <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />,
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  minus: <path d="M3.5 8h9" />,
  fit: (
    <path d="M2.5 6V3.5a1 1 0 0 1 1-1H6M10 2.5h2.5a1 1 0 0 1 1 1V6M13.5 10v2.5a1 1 0 0 1-1 1H10M6 13.5H3.5a1 1 0 0 1-1-1V10" />
  ),
  stepBack: <path d="M12 3.2v9.6L5.5 8zM4 3v10" />,
  stepForward: <path d="M4 3.2v9.6L10.5 8zM12 3v10" />,
  instructions: <path d="M3 4h10M3 8h10M3 12h6" />,
  toolset: (
    <>
      <rect x="2.5" y="2.5" width="11" height="4" rx="1" />
      <rect x="2.5" y="9.5" width="11" height="4" rx="1" />
    </>
  ),
  decision: <path d="M8 2l6 6-6 6-6-6z" />,
  arrowRight: <path d="M2.5 8h10M9 4.5L12.5 8 9 11.5" />,
  user: (
    <>
      <circle cx="8" cy="5.5" r="2.6" />
      <path d="M3 13.5c.6-2.4 2.6-3.6 5-3.6s4.4 1.2 5 3.6" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof paths;

type Props = {
  name: IconName;
  /** Pixel size; the stroke stays 1.5px at 16. */
  size?: number;
  /** Accessible name. Omit for decorative icons next to text. */
  label?: string;
  className?: string;
};

export function Icon({ name, size = 16, label, className }: Props) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ? `shrink-0 ${className}` : "shrink-0"}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {paths[name]}
    </svg>
  );
}
