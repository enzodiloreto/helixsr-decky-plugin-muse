// Material You (M3) inspired design tokens for the plugin's custom chrome.
//
// Decky's native controls (buttons, dropdowns, sliders) keep Steam styling so
// gamepad focus keeps working. Everything custom — cards, chips, steppers,
// banners, log views, type hierarchy — follows these tonal roles instead of
// ad-hoc colors. Dark scheme, tuned to sit inside Steam's dark UI.
import { CSSProperties } from "react";

export const M3 = {
  color: {
    // Primary family (baseline M3 dark, violet): actions-in-progress, highlights.
    primary: "#D0BCFF",
    onPrimary: "#381E72",
    primaryContainer: "#4F378B",
    onPrimaryContainer: "#EADDFF",
    // Secondary family: neutral accents, chips, secondary actions.
    secondaryContainer: "#4A4458",
    onSecondaryContainer: "#E8DEF8",
    // Tertiary family (pink): update availability, special highlights.
    tertiaryContainer: "#633B48",
    onTertiaryContainer: "#FFD8E4",
    // Surfaces: layered tonal fills, lowest first.
    surface: "#141218",
    surfaceContainerLowest: "#0F0D13",
    surfaceContainerLow: "#1D1B20",
    surfaceContainer: "#211F26",
    surfaceContainerHigh: "#2B2930",
    onSurface: "#E6E0E9",
    onSurfaceVariant: "#CAC4D0",
    outline: "#938F99",
    outlineVariant: "#49454F",
    // Extended: success has no M3 role, so a green tonal pair in the same spirit.
    successContainer: "#2A4A38",
    onSuccessContainer: "#BEE9C9",
    warningContainer: "#4A3A1F",
    onWarningContainer: "#F5DFA6",
    errorContainer: "#8C1D18",
    onErrorContainer: "#F2B8B8",
  },
  shape: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 28,
    full: 999,
  },
  type: {
    headline: { fontSize: 18, fontWeight: 700, letterSpacing: 0 } as CSSProperties,
    title: { fontSize: 14, fontWeight: 600, letterSpacing: 0 } as CSSProperties,
    body: { fontSize: 13, fontWeight: 400, letterSpacing: 0 } as CSSProperties,
    caption: { fontSize: 11.5, fontWeight: 400, letterSpacing: 0.2 } as CSSProperties,
    label: { fontSize: 12, fontWeight: 500, letterSpacing: 0.3 } as CSSProperties,
  },
  space: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
  },
  motion: {
    quick: "opacity 150ms ease, max-height 200ms ease",
    bar: 0.4,
  },
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  tabular: "tabular-nums" as const,
} as const;

export type M3Tone =
  | "primary"
  | "secondary"
  | "tertiary"
  | "success"
  | "warning"
  | "error"
  | "neutral";
