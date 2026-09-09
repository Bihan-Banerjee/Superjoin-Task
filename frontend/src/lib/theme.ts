import { useEffect, useState } from "react";

/**
 * Light and dark, chosen or inherited.
 *
 * Three states rather than two. "system" is the default and stamps nothing on the document,
 * so the CSS media query decides; picking light or dark writes `data-theme` on the root,
 * which the token file uses to win over the query in both directions.
 *
 * The choice is written to localStorage and read again before first paint by a small script
 * in index.html, because applying it from React would flash the wrong theme for one frame.
 */
export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "fkl-theme";

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private browsing, or storage disabled. The theme still applies for this page view.
  }
}

export function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    /* fall through to the default */
  }
  return "system";
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return [theme, setTheme];
}

/** What the toggle is currently showing, resolving "system" against the media query. */
export function resolvedTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
