// Display-only Material You primitives (cards, chips, stepper, divider).
// No focusable elements: gamepad navigation stays entirely on Decky controls.
import { CSSProperties, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Focusable } from "@decky/ui";
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
        // Not nowrap: a long chip wraps inside itself instead of pushing the
        // row (and the text next to it) out of shape.
        overflowWrap: "anywhere",
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

/**
 * Gamepad-readable long text: newest lines visible with auto-scroll, older
 * lines revealed through a focusable button (inner scroll divs are unreachable
 * by gamepad, which navigates focusables, not scroll offsets).
 */
export function PagedText(props: { text: string; pageSize?: number; maxHeight?: number }) {
  const { text, pageSize = 40, maxHeight = 240 } = props;
  const lines = useMemo(() => text.split("\n"), [text]);
  const [shown, setShown] = useState(pageSize);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const visible = lines.slice(-Math.min(shown, lines.length));
  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <div style={{ width: "100%", maxWidth: "100%" }}>
      <div
        ref={boxRef}
        style={{
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          fontSize: 12,
          fontFamily: M3.mono,
          maxHeight,
          overflowY: "auto",
          overflowX: "hidden",
        }}
      >
        {visible.join("\n")}
      </div>
      {lines.length > shown && (
        <MButton variant="text" onClick={() => setShown((s) => s + pageSize)}>
          {`Show older (${lines.length - shown} more lines)`}
        </MButton>
      )}
      {lines.length > pageSize && (
        <div style={{ ...M3.type.caption, color: M3.color.onSurfaceVariant }}>
          showing last {Math.min(shown, lines.length)} of {lines.length} lines
        </div>
      )}
    </div>
  );
}

export type MButtonVariant =
  | "filled"
  | "tonal"
  | "outlined"
  | "text"
  | "danger"
  | "danger-outline";

const variantStyle: Record<MButtonVariant, CSSProperties> = {
  filled: { background: M3.color.primary, color: M3.color.onPrimary },
  tonal: {
    background: M3.color.secondaryContainer,
    color: M3.color.onSecondaryContainer,
  },
  outlined: {
    background: "transparent",
    border: `1px solid ${M3.color.outline}`,
    color: M3.color.primary,
  },
  text: { background: "transparent", color: M3.color.primary },
  danger: {
    background: M3.color.errorContainer,
    color: M3.color.onErrorContainer,
  },
  "danger-outline": {
    background: "transparent",
    border: `1px solid ${M3.color.errorContainer}`,
    color: M3.color.onErrorContainer,
  },
};

const disabledStyle: CSSProperties = {
  background: "rgba(230, 224, 233, 0.12)",
  color: "rgba(230, 224, 233, 0.38)",
  border: "none",
  cursor: "default",
};

/**
 * Material You button with full gamepad support: Focusable participates in
 * Steam's spatial navigation, onActivate fires on gamepad OK, onClick covers
 * mouse/touch. Disabled buttons render non-focusable so gamepad skips them.
 */
export function MButton(props: {
  variant?: MButtonVariant;
  disabled?: boolean;
  onClick: () => void;
  description?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const { variant = "tonal", disabled, onClick, description, children, style } = props;
  // Inline styles cannot do :hover, so hover/focus feedback is tracked state.
  // Gamepad focus keeps Steam's native ring via Focusable on top of this.
  const [hover, setHover] = useState(false);
  const [focused, setFocused] = useState(false);
  const lit = !disabled && (hover || focused);
  const body = (
    <>
      <div style={{ ...M3.type.label, fontSize: 14, textAlign: "center" }}>{children}</div>
      {description ? (
        <div
          style={{
            ...M3.type.caption,
            textAlign: "center",
            opacity: 0.8,
            overflowWrap: "anywhere",
          }}
        >
          {description}
        </div>
      ) : null}
    </>
  );
  const box: CSSProperties = {
    width: "100%",
    maxWidth: "100%",
    boxSizing: "border-box",
    minHeight: 44,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
    padding: "10px 16px",
    margin: "4px 0",
    borderRadius: M3.shape.full,
    cursor: disabled ? "default" : "pointer",
    userSelect: "none",
    transition: "filter 120ms ease, background-color 120ms ease",
    ...(lit ? { filter: "brightness(1.22)" } : {}),
    ...variantStyle[variant],
    ...(disabled ? disabledStyle : {}),
    ...style,
  };
  if (disabled) {
    return (
      <div aria-disabled style={box}>
        {body}
      </div>
    );
  }
  const fire = () => {
    onClick();
  };
  return (
    <Focusable
      style={box}
      onActivate={() => fire()}
      onClick={() => fire()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onKeyDown={(e: any) => {
        if (e.key === "Enter") fire();
      }}
    >
      {body}
    </Focusable>
  );
}
