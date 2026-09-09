import { resolvedTheme, useTheme, type Theme } from "../lib/theme";

/**
 * A three-way control rather than a switch.
 *
 * "System" is a real preference and the default, so collapsing this to a two-state toggle
 * would make the common case unreachable once someone had touched it.
 */
const OPTIONS: { value: Theme; label: string; glyph: string }[] = [
  { value: "light", label: "Light", glyph: "☀" },
  { value: "system", label: "Match the system", glyph: "◐" },
  { value: "dark", label: "Dark", glyph: "☾" },
];

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [theme, setTheme] = useTheme();

  return (
    <div
      className={compact ? "theme-toggle theme-toggle--compact" : "theme-toggle"}
      role="radiogroup"
      aria-label="Colour theme"
    >
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={theme === option.value}
          aria-label={option.label}
          title={option.label}
          className={theme === option.value ? "theme-toggle__pick is-on" : "theme-toggle__pick"}
          onClick={() => setTheme(option.value)}
        >
          <span aria-hidden="true">{option.glyph}</span>
        </button>
      ))}
      <span className="visually-hidden">
        Currently showing {resolvedTheme(theme)} colours.
      </span>
    </div>
  );
}
