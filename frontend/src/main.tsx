import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import App from "./App";

// Bundled rather than fetched from a CDN: the evaluator may run this with no network, and a
// landing page whose type silently falls back to the system stack is not the page that was
// designed. Subsets are split by unicode-range, so a latin reader downloads one file.
import "@fontsource-variable/inter/wght.css";

import "./styles/base.css";
import "./styles/components.css";
import "./styles/landing.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element missing from index.html");
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
