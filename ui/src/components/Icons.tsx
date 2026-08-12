import type { ReactNode } from "react";

type IconName =
  | "pulse"
  | "cases"
  | "tower"
  | "arrow"
  | "refresh"
  | "spark"
  | "sun"
  | "moon";

export function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    pulse: <path d="M3 12h4l2.2-5.5L13 18l2.4-6H21" />,
    cases: (
      <>
        <rect x="4" y="5" width="16" height="14" rx="3" />
        <path d="M8 5V3h8v2M8 10h8M8 14h5" />
      </>
    ),
    tower: (
      <>
        <path d="M5 20V9l7-5 7 5v11" />
        <path d="M9 20v-5h6v5M9 10h.01M15 10h.01" />
      </>
    ),
    arrow: <path d="M5 12h14M14 7l5 5-5 5" />,
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M19 12a7 7 0 1 1-2-5" />
      </>
    ),
    spark: (
      <>
        <path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4L12 3Z" />
        <path d="m18.5 14 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z" />
      </>
    ),
    sun: (
      <>
        <circle cx="12" cy="12" r="3.5" />
        <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" />
      </>
    ),
    moon: <path d="M20.2 15.1A8.4 8.4 0 0 1 8.9 3.8 8.5 8.5 0 1 0 20.2 15.1Z" />,
  };

  return (
    <svg
      aria-hidden="true"
      className="icon"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
    >
      {paths[name]}
    </svg>
  );
}
