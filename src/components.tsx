// Display-only Material You primitives (cards, chips, stepper, divider).
// No focusable elements: gamepad navigation stays entirely on Decky controls.
import { CSSProperties, ReactNode } from "react";
import { M3, M3Tone } from "./theme";

const tonePair: Record<M3Tone, [string, string]> = {
  primary: [M3.color.primaryContainer, M3.color.onPrimaryContainer],
  secondary: [M3.color.secondaryContainer, M3.color.onSecondaryContainer],
  tertiary: [M3.color.tertiaryContainer, M3.color.onTertiaryContainer],
  success: [M3.color.successContainer, M3.color.onSuccessContainer],
  warning: [M3.color.warningContainer, M3.color.onWarningContainer],
  error: [M3.color.errorContainer, M3.color.onErrorContainer],
  neutral: [M3.color.surfaceContainerHigh, M3.color.onSurface],
};

export function Card(props: {
  variant?: "filled" | "outlined" | "elevated";
  tone?: M3Tone;
  radius?: number;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const { variant = "filled", tone = "neutral", radius = M3.shape.lg, children, style } = props;
  const [bg, fg] = tonePair[tone];
  const base: CSSProperties =
    variant === "outlined"
      ? {
          background: "transparent",
          border: `1px solid ${M3.color.outlineVariant}`,
          color: M3.color.onSurface,
        }
      : variant === "elevated"
        ? {
            background: M3.color.surfaceContainer,
            color: M3.color.onSurface,
            boxShadow: "0 2px 10px rgba(0, 0, 0, 0.45)",
          }
        : { background: bg, color: fg };
  return (
    <div
      style={{
        borderRadius: radius,
        padding: M3.space.md,
        // Contain padding/borders inside the panel width (Steam content-box
        // + width:100% otherwise overflows the right edge by 2x padding),
        // never exceed it, and wrap unbreakable strings (paths, log lines).
        boxSizing: "border-box",
        maxWidth: "100%",
        minWidth: 0,
        overflowWrap: "anywhere",
        ...base,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Chip(props: { tone?: M3Tone; children: ReactNode }) {
  const { tone = "neutral", children } = props;
  const [bg, fg] = tonePair[tone];
  return (
    <span
      style={{
        display: "inline-block",
        background: bg,
        color: fg,
        borderRadius: M3.shape.full,
        padding: "3px 10px",
        maxWidth: "100%",
        ...M3.type.caption,
        fontWeight: 500,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function Stepper(props: { step: number; total?: number }) {
  const { step, total = 4 } = props;
  const dots = [];
  for (let i = 1; i <= total; i++) {
    const done = i < step;
    const current = i === step;
    dots.push(
      <span
        key={i}
        style={{
          width: current ? 22 : 8,
          height: 8,
          borderRadius: M3.shape.full,
          background: done || current ? M3.color.primary : M3.color.outlineVariant,
          opacity: done ? 0.55 : 1,
          transition: "width 200ms ease, background 200ms ease",
        }}
      />
    );
  }
  return <div style={{ display: "flex", alignItems: "center", gap: 6 }}>{dots}</div>;
}

export function Divider() {
  return (
    <div
      style={{
        height: 1,
        background: M3.color.outlineVariant,
        margin: `${M3.space.sm}px 0`,
        opacity: 0.7,
      }}
    />
  );
}
