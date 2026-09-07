import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "../lib/api";
import { formatNumber } from "../lib/format";

const SECTIONS: { heading: string; links: { to: string; label: string; count?: keyof Counts }[] }[] = [
  {
    heading: "Corpus",
    links: [
      { to: "/documents", label: "Documents", count: "documents" },
      { to: "/facts", label: "Facts", count: "facts" },
      { to: "/relations", label: "Relations", count: "relations" },
    ],
  },
  {
    heading: "Review",
    links: [
      { to: "/cases", label: "Cases" },
      { to: "/registry", label: "Registry", count: "measures" },
      { to: "/evaluation", label: "Evaluation" },
    ],
  },
];

interface Counts {
  documents: number;
  facts: number;
  relations: number;
  measures: number;
}

const TITLES: Record<string, string> = {
  "/documents": "Documents",
  "/facts": "Facts",
  "/relations": "Relations",
  "/cases": "Required cases",
  "/registry": "Registry",
  "/evaluation": "Evaluation",
};

export default function Shell() {
  const location = useLocation();

  const { data: evaluation } = useQuery({
    queryKey: ["evaluation"],
    queryFn: api.getEvaluation,
    refetchInterval: 20_000,
  });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });

  const counts: Counts = {
    documents: evaluation?.corpus.documents ?? 0,
    facts: evaluation?.grounding.facts_kept ?? 0,
    relations: evaluation?.relations.total ?? 0,
    measures: evaluation?.registry.measures ?? 0,
  };

  const base = `/${location.pathname.split("/")[1] ?? ""}`;
  const title = TITLES[base] ?? "Fact Knowledge Layer";

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar__brand">
          <span className="sidebar__title">Fact Knowledge Layer</span>
          <span className="sidebar__subtitle">Grounded facts across documents</span>
        </div>

        <div className="sidebar__nav">
          {SECTIONS.map((section) => (
            <div key={section.heading}>
              <div className="sidebar__section">{section.heading}</div>
              {section.links.map((link) => (
                <NavLink key={link.to} to={link.to} className="nav-link">
                  <span>{link.label}</span>
                  {link.count ? (
                    <span className="nav-link__count">{formatNumber(counts[link.count], 0)}</span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          ))}
        </div>

        <div className="sidebar__footer">
          {health ? (
            <dl className="meta-list">
              <dt>Model</dt>
              <dd>
                {health.provider}
                {health.provider_configured ? "" : " (not configured)"}
              </dd>
              <dt>Embeddings</dt>
              <dd>local</dd>
            </dl>
          ) : null}
        </div>
      </nav>

      <div className="main">
        <header className="main__header">
          <h1>{title}</h1>
          {health && !health.provider_configured ? (
            <span className="chip" title="Set a provider key in backend/.env to ingest new PDFs">
              No model configured — browsing only
            </span>
          ) : null}
        </header>
        <div className="main__body">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
