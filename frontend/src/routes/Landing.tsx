import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import { formatNumber } from "../lib/format";

/**
 * The front door.
 *
 * Built as a dark instrument rather than a marketing page: near-black ground, hairline
 * borders instead of shadows, a narrow 400 to 590 weight band at tight tracking, and one
 * chromatic element on the whole page. The restraint is the point. A tool whose entire claim
 * is that it refuses to guess should not open with something that looks generated.
 *
 * It does not follow the light and dark toggle. The workbench does, because someone reads
 * dense tables there for a long time and that is a real preference; this page is one argument
 * made once, and the argument is made in the dark. The toggle lives in the workbench header.
 *
 * Every figure in the closing paragraph is read from the running layer. If the API is not
 * answering, the sentence drops to its unmeasured form rather than printing a plausible
 * number, which would undermine the only claim the project actually rests on.
 */
export default function Landing() {
  const { data: evaluation } = useQuery({
    queryKey: ["evaluation"],
    queryFn: api.getEvaluation,
    retry: false,
  });
  const { data: documents } = useQuery({
    queryKey: ["documents"],
    queryFn: api.listDocuments,
    retry: false,
  });

  const progress = useScrollProgress();
  useDarkPage();
  useReveal([evaluation, documents]);

  const counts = evaluation
    ? {
        facts: formatNumber(evaluation.grounding.facts_kept, 0),
        documents: formatNumber(evaluation.corpus.documents, 0),
        relations: formatNumber(evaluation.relations.total, 0),
        cross: formatNumber(evaluation.relations.cross_document, 0),
        rate: `${Math.round(evaluation.grounding.pass_rate * 100)}%`,
        rejected: formatNumber(evaluation.grounding.candidates_rejected, 0),
      }
    : null;

  return (
    <div className="lp">
      <header className="lp-nav">
        <div className="lp-nav__inner">
          <Link to="/" className="lp-nav__mark">
            <span className="lp-nav__glyph" aria-hidden="true">
              ¶
            </span>
            Fact Knowledge Layer
          </Link>
          <nav className="lp-nav__links">
            <a href="#problem">
              <i>01</i>Problem
            </a>
            <a href="#method">
              <i>02</i>Method
            </a>
            <a href="#proof">
              <i>03</i>Proof
            </a>
            <Link className="lp-pill" to="/documents">
              Open workbench
            </Link>
          </nav>
        </div>
        <span className="lp-nav__progress" style={{ transform: `scaleX(${progress})` }} />
      </header>

      <main>
        <section className="lp-hero">
          <div className="lp__shell">
            <div className="lp-hero__copy">
              <p className="lp-eyebrow lp-reveal">Fact reconciliation across documents</p>
              <h1 className="lp-display lp-reveal">
                Two documents. One fact.
                <br />
                <span>Nothing on either page says so.</span>
              </h1>
              <p className="lp-lede lp-reveal">
                An annual report prints <b>81,415</b> under a note reading &ldquo;amounts in
                Indian Rupees in million&rdquo;. An earnings deck prints <b>8,142</b> against
                an axis marked ₹ Cr. Different digits, different unit, different wording, one
                revenue. A reader sees it in a minute. A system has to be told how.
              </p>
              <div className="lp-hero__actions lp-reveal">
                <Link className="lp-cta" to="/cases">
                  Read the four cases
                </Link>
                <Link className="lp-link" to="/relations">
                  Browse every relation <Arrow />
                </Link>
              </div>
            </div>
          </div>

          <div className="lp-hero__stage">
            <div className="lp__shell">
              <ReconciliationFrame />
            </div>
          </div>
        </section>

        <div className="lp__shell">
          <CorpusStrip documents={documents?.documents} />
        </div>

        <div className="lp__shell">
          <section className="lp-section" id="problem">
            <div className="lp-split">
              <div className="lp-split__copy">
                <p className="lp-eyebrow lp-reveal">01 The problem</p>
                <h2 className="lp-h2 lp-reveal">
                  A page does not say
                  <br />
                  what it means.
                </h2>
                <p className="lp-lede lp-reveal">
                  Extraction is the easy half. The scale sits in a note twelve pages earlier,
                  the period is called FY24 in one document and 2024/25 in another, and a
                  chart contributes its numbers and its labels to the text layer as unordered
                  tokens with no stated relationship between them.
                </p>
                <p className="lp-lede lp-reveal">
                  Comparing text to text produces nonsense at this point. Everything has to be
                  resolved into a form that can be compared before anything is compared.
                </p>
              </div>

              <div className="lp-raw lp-reveal">
                <div className="lp-raw__head">
                  <span>earnings deck, page 9</span>
                  <span>text layer, verbatim</span>
                </div>
                <p className="lp-raw__tokens">
                  Revenue from services <u>7,054</u> <u>7,224</u> <u>8,142</u> FY22 FY23 FY24 ₹
                  Cr
                </p>
                <p className="lp-raw__note">
                  Three values, three periods, and nothing binding one to another. The reading
                  order is the order the glyphs were drawn in, which is not the order they are
                  meant to be read in. Recovering the pairing needs the geometry of the page,
                  not its text.
                </p>
              </div>
            </div>
          </section>

          <section className="lp-section" id="method">
            <p className="lp-eyebrow lp-reveal">02 The method</p>
            <h2 className="lp-h2 lp-reveal">Four decisions, in the order they matter</h2>

            <ol className="lp-steps">
              {STEPS.map((step, index) => (
                <li className="lp-step lp-reveal" key={step.title}>
                  <span className="lp-step__num">{String(index + 1).padStart(2, "0")}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </li>
              ))}
            </ol>
          </section>

          <section className="lp-section" id="proof">
            <p className="lp-eyebrow lp-reveal">03 The proof</p>
            <h2 className="lp-h2 lp-reveal">What the brief asks it to show</h2>

            <div className="lp-proofs">
              {PROOFS.map((proof) => (
                <article className="lp-proof lp-reveal" key={proof.title}>
                  <h3>{proof.title}</h3>
                  <p>{proof.body}</p>
                  <div className="lp-proof__foot">
                    <span className={`lp-badge lp-badge--${proof.tone}`}>{proof.verdict}</span>
                  </div>
                </article>
              ))}
            </div>

            <p className="lp-running lp-reveal">
              {counts ? (
                <>
                  The layer currently holds <Num>{counts.facts}</Num> grounded facts read from{" "}
                  <Num>{counts.documents}</Num> documents, and <Num>{counts.relations}</Num>{" "}
                  relations between them, of which <Num tone="tag">{counts.cross}</Num> join
                  two different sources. <Num tone="good">{counts.rate}</Num> of proposed facts
                  survived verification against the page they came from. The other{" "}
                  <Num tone="bad">{counts.rejected}</Num> were refused, and each one kept the
                  reason it was refused.
                </>
              ) : (
                <>
                  Every figure in this paragraph is read from the running layer. The API is not
                  answering at the moment, so there is nothing here rather than an estimate
                  standing in for something measured.
                </>
              )}
            </p>
          </section>

          <section className="lp-close">
            <h2 className="lp-h2 lp-reveal">Point it at a filing it has never seen.</h2>
            <p className="lp-lede lp-reveal">
              No rule, prompt, or schema in this project names a document. Predicates and
              qualifier keys are discovered from whatever arrives and resolved against a
              registry, so an unfamiliar filing needs no migration and no new code.
            </p>
            <div className="lp-hero__actions lp-reveal">
              <Link className="lp-ghost" to="/documents">
                Upload a PDF
              </Link>
              <Link className="lp-link" to="/evaluation">
                See what it got wrong <Arrow />
              </Link>
            </div>
          </section>

          <footer className="lp-foot">
            <p>
              Facts are extracted, verified against the page they came from, normalised, and
              only then compared. Rules answer first and record why; the model is asked only
              about what the rules cannot settle.
            </p>
            <Link className="lp-link" to="/documents">
              Open workbench <Arrow />
            </Link>
          </footer>
        </div>
      </main>
    </div>
  );
}

/**
 * The reconciliation, drawn the way the workbench draws it.
 *
 * Linear's own site frames real product UI rather than an illustration, and the same applies
 * here: this is the layout of a relation card, with the flagship pair in it. The rule named
 * underneath is the rule that actually decides this comparison.
 */
function ReconciliationFrame() {
  return (
    <div className="lp-frame lp-reveal">
      <div className="lp-frame__bar">
        <span className="lp-frame__id">rule: values-agree</span>
        <div className="lp-badges">
          <span className="lp-badge lp-badge--good">corroborates</span>
          <span className="lp-badge lp-badge--tag">unit_scale</span>
          <span className="lp-badge">across documents</span>
          <span className="lp-badge">no model call</span>
        </div>
      </div>

      <div className="lp-pair">
        <div className="lp-side lp-side--left">
          <p className="lp-side__source">
            <b>Annual report FY24</b> <span>page 6</span>
          </p>
          <p className="lp-side__measure">Revenue from services</p>
          <p className="lp-side__value">81,415</p>
          <p className="lp-side__unit">INR, stated in million</p>
          <p className="lp-side__quote">
            &ldquo;All amounts in Indian Rupees in million unless otherwise stated&rdquo;
          </p>
        </div>

        <div className="lp-join" aria-hidden="true">
          <span>=</span>
        </div>

        <div className="lp-side lp-side--right">
          <p className="lp-side__source">
            <b>Q4 FY24 earnings deck</b> <span>page 9</span>
          </p>
          <p className="lp-side__measure">Revenue from services</p>
          <p className="lp-side__value">8,142</p>
          <p className="lp-side__unit">INR, axis marked ₹ Cr</p>
          <p className="lp-side__quote">
            &ldquo;Revenue from services 7,054 7,224 8,142 FY22 FY23 FY24&rdquo;
          </p>
        </div>
      </div>

      <p className="lp-frame__verdict">
        Both resolve to <b>₹81.42 billion</b> for the year ended 31 March 2024. The difference
        is entirely the scale each document declares, so the comparison is settled
        mechanically by <code>values-agree</code> on the normalised value. Nothing was asked of
        a model.
      </p>
    </div>
  );
}

/** What is actually loaded, named. Empty until the API answers, never filled with examples. */
function CorpusStrip({ documents }: { documents?: { id: number; title: string }[] }) {
  if (!documents?.length) return null;
  return (
    <div className="lp-strip lp-reveal">
      <p className="lp-strip__label">Currently in the layer</p>
      <div className="lp-strip__row">
        {documents.map((document) => (
          <span key={document.id}>{document.title}</span>
        ))}
      </div>
    </div>
  );
}

function Num({ children, tone }: { children: ReactNode; tone?: "good" | "bad" | "tag" }) {
  return <span className={tone ? `lp-num lp-num--${tone}` : "lp-num"}>{children}</span>;
}

function Arrow() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path
        d="M2.5 6h7m0 0L6.75 3.25M9.5 6 6.75 8.75"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Marks the document while this page is mounted.
 *
 * The page is black to the edges, and an overscroll bounce would otherwise reveal the
 * workbench background behind it. The attribute is removed on unmount so navigating into the
 * workbench returns it to the theme the toggle chose.
 */
function useDarkPage() {
  useEffect(() => {
    document.documentElement.setAttribute("data-page", "landing");
    return () => document.documentElement.removeAttribute("data-page");
  }, []);
}

/** Fraction of the page scrolled, for the hairline under the navigation. */
function useScrollProgress(): number {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    const update = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(1, window.scrollY / max) : 0);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);
  return progress;
}

/**
 * Adds `is-visible` as things scroll into view.
 *
 * No run-once guard: StrictMode invokes effects twice, and a guard that skips the second pass
 * leaves the observer disconnected and the page stuck at opacity zero. Re-running on the
 * query signals picks up content that arrives after the first pass.
 */
function useReveal(signals: unknown[]) {
  useEffect(() => {
    const targets = Array.from(document.querySelectorAll(".lp-reveal:not(.is-visible)"));
    if (!targets.length) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      targets.forEach((node) => node.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        }),
      { rootMargin: "0px 0px -8% 0px" },
    );
    targets.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, signals);
}

const STEPS = [
  {
    title: "Resolve it before comparing it",
    body: "Every figure becomes a value in a base unit with a dated interval attached, and the words the document used are kept beside it. Comparison happens on the resolved form. Text is never compared with text.",
  },
  {
    title: "Verify the evidence, do not trust it",
    body: "The quote is located in the page it was claimed from, and the value is checked against the page rather than against the quote. A fabricated figure cannot vouch for itself. Anything that fails becomes a row in the rejection ledger.",
  },
  {
    title: "Let rules answer first",
    body: "Period, scale, currency, scope and basis are settled mechanically. That is free, reproducible, and explainable in a sentence a reader can check against the source. The model is asked only about what the rules cannot settle.",
  },
  {
    title: "Keep the schema as data",
    body: "Nothing in the code enumerates what can be measured. A document introduces a phrase and the registry either recognises it or admits a new canonical measure, so an unfamiliar filing needs no migration.",
  },
];

const PROOFS = [
  {
    tone: "good",
    title: "Corroboration",
    body: "The same claim in two sources, in different units and different words, recognised as one fact rather than two.",
    verdict: "corroborates",
  },
  {
    tone: "bad",
    title: "A real contradiction",
    body: "Same measure, entity and period. Values that disagree, and no dimension that accounts for the gap. Scored by severity rather than asserted flatly.",
    verdict: "contradicts",
  },
  {
    tone: "tag",
    title: "Explained by context",
    body: "Figures that differ for a nameable reason: period, scale, scope, basis or vintage. The dimension responsible is named, so the reconciliation can be checked rather than believed.",
    verdict: "reconciled by context",
  },
  {
    tone: "teal",
    title: "Where it failed",
    body: "Counted, not described. Every refused candidate keeps the reason it was refused and the evidence that produced it, and the rate is on the evaluation page.",
    verdict: "rejection ledger",
  },
];
