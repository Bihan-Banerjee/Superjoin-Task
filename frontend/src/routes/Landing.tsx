import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import ThemeToggle from "../components/ThemeToggle";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";

/**
 * The front door, laid out as a marked-up document.
 *
 * The product reads documents and marks evidence on them, so the page takes the shape of a
 * marked-up page: a numbered margin rail, one wide text column, highlighted spans where a
 * figure matters, and marginal notes instead of boxes. That is deliberately not the shape of
 * a typical product site, and it makes the layout say something about the tool rather than
 * decorate it.
 *
 * Numbers are read from the API and written into the prose rather than stacked into tiles.
 * If the API is unreachable the sentence drops to its unmeasured form; a front page that
 * invents plausible figures when the backend is down would undermine the only claim the
 * project actually rests on.
 */
export default function Landing() {
  const { data: evaluation } = useQuery({
    queryKey: ["evaluation"],
    queryFn: api.getEvaluation,
    retry: false,
  });
  const progress = useScrollProgress();
  useReveal(evaluation);

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
    <div className="doc">
      <header className="topbar">
        <div className="topbar__inner">
          <Link to="/" className="topbar__mark">
            <span className="topbar__glyph" aria-hidden="true">
              ¶
            </span>
            <span className="topbar__name">Fact Knowledge Layer</span>
          </Link>
          <nav className="topbar__nav">
            <a href="#problem">
              <i>01</i> Problem
            </a>
            <a href="#method">
              <i>02</i> Method
            </a>
            <a href="#proof">
              <i>03</i> Proof
            </a>
            <ThemeToggle compact />
            <Link className="stamp" to="/documents">
              Open workbench
            </Link>
          </nav>
        </div>
        <span className="topbar__rule" style={{ transform: `scaleX(${progress})` }} />
      </header>

      <main className="sheet">
        <section className="folio" id="problem">
          <aside className="folio__rail" aria-hidden="true">
            <span className="folio__num">01</span>
          </aside>

          <div className="folio__body">
            <p className="kicker">The problem</p>
            <h1 className="display reveal">
              Two documents. One fact.
              <br />
              <mark className="mk mk-1">Nothing</mark> in common on the page.
            </h1>

            <p className="lede reveal">
              An annual report prints <b>81,415</b> under a note reading &ldquo;amounts in
              Indian Rupees in million&rdquo;. An earnings deck prints <b>8,142</b> in crore.
              Different digits, different unit, different wording, and the same revenue. A
              reader spots it in a minute. A system has to be told how.
            </p>

            <Clippings />

            <p className="aside-note reveal">
              Easy to describe, hard to get right: the only thing the two figures share is a
              meaning that neither of them writes down.
            </p>
          </div>
        </section>

        <section className="folio folio--tint" id="method">
          <aside className="folio__rail" aria-hidden="true">
            <span className="folio__num">02</span>
          </aside>

          <div className="folio__body">
            <p className="kicker">The method</p>
            <h2 className="heading reveal">Four decisions, in the order they matter</h2>

            <ol className="steps">
              {STEPS.map((step, index) => (
                <li className="step reveal" key={step.title} style={{ "--i": index } as never}>
                  <span className={`step__bead step__bead--${index + 1}`} aria-hidden="true" />
                  <div className="step__text">
                    <h3>{step.title}</h3>
                    <p>{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="folio" id="proof">
          <aside className="folio__rail" aria-hidden="true">
            <span className="folio__num">03</span>
          </aside>

          <div className="folio__body">
            <p className="kicker">The proof</p>
            <h2 className="heading reveal">What the brief asks it to show</h2>

            <div className="proofs">
              {PROOFS.map((proof, index) => (
                <article className={`proof proof--${proof.tone} reveal`} key={proof.title}>
                  <span className="proof__index">{String(index + 1).padStart(2, "0")}</span>
                  <h3 className="proof__title">{proof.title}</h3>
                  <p className="proof__body">{proof.body}</p>
                  <span className="proof__verdict">{proof.verdict}</span>
                </article>
              ))}
            </div>

            <p className="running reveal">
              {counts ? (
                <>
                  Right now the layer holds <Num>{counts.facts}</Num> grounded facts read from{" "}
                  <Num>{counts.documents}</Num> documents, and <Num>{counts.relations}</Num>{" "}
                  relations between them, of which <Num tone="accent">{counts.cross}</Num> span
                  two different sources. <Num tone="good">{counts.rate}</Num> of proposed facts
                  survived verification against the page they came from. The other{" "}
                  <Num tone="bad">{counts.rejected}</Num> were refused, and each one is a row
                  saying why.
                </>
              ) : (
                <>
                  Every figure on this page is read from the running layer. The API is not
                  answering at the moment, so there is nothing to report here rather than an
                  estimate standing in for one.
                </>
              )}
            </p>

            <div className="ctas reveal">
              <Link className="stamp stamp--lg" to="/cases">
                Read the four cases
              </Link>
              <Link className="ghost" to="/evaluation">
                See what it got wrong
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="colophon">
        <span className="colophon__mark" aria-hidden="true">
          ¶
        </span>
        <p>
          Facts are extracted, grounded in the page they came from, normalised, and compared.
          Nothing is hard-coded to these documents: point it at a filing it has never seen and
          it answers from that one.
        </p>
        <ThemeToggle />
      </footer>
    </div>
  );
}

/** The reconciliation, drawn as two clippings tied together. */
function Clippings() {
  return (
    <div className="clips reveal">
      <div className="clips__row">
        <figure className="clip">
          <figcaption>
            Annual report <span>p.6</span>
          </figcaption>
          <p className="clip__line">
            Revenue from services <mark className="mk mk-2">81,415</mark>
          </p>
          <p className="clip__note">note: amounts in Indian Rupees in million</p>
        </figure>

        <div className="tie" aria-hidden="true">
          <span className="tie__thread" />
          <span className="tie__seal">=</span>
        </div>

        <figure className="clip">
          <figcaption>
            Earnings deck <span>p.9</span>
          </figcaption>
          <p className="clip__line">
            Revenue from services <mark className="mk mk-3">8,142</mark>
          </p>
          <p className="clip__note">axis: ₹ Cr</p>
        </figure>
      </div>

      <p className="clips__verdict">
        <b>₹81.42 billion</b> either way. Decided by rule, on the unit scale, with no model
        call.
      </p>
    </div>
  );
}

function Num({ children, tone }: { children: ReactNode; tone?: "accent" | "good" | "bad" }) {
  return <span className={tone ? `num num--${tone}` : "num"}>{children}</span>;
}

/** Fraction of the page scrolled, for the rule under the top bar. */
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
 * Adds `is-visible` as things scroll in.
 *
 * No run-once guard: StrictMode invokes effects twice, and a guard that skips the second
 * pass leaves the observer disconnected and the page stuck at opacity zero.
 */
function useReveal(signal?: unknown) {
  useEffect(() => {
    const targets = Array.from(document.querySelectorAll(".reveal:not(.is-visible)"));
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
  }, [signal]);
}

const STEPS = [
  {
    title: "Resolve it before comparing it",
    body: "Every figure becomes a value in a base unit with a dated interval attached, and the words the document used are kept beside it. Comparison happens on the resolved form. Text is never compared with text.",
  },
  {
    title: "Verify the evidence, do not trust it",
    body: "The quote is located in the page, and the value is checked against the page rather than against the quote. A fabricated figure cannot vouch for itself, and anything that fails becomes a row in the rejection ledger.",
  },
  {
    title: "Let rules answer first",
    body: "Period, scale, currency, scope and basis are settled mechanically. That is free, reproducible, and explainable in a sentence a reader can check. The model is asked only about what the rules cannot settle.",
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
    tone: "warn",
    title: "Explained by context",
    body: "Figures that differ for a nameable reason: period, scale, scope, basis or vintage. The dimension responsible is named, so the reconciliation can be checked.",
    verdict: "reconciled by context",
  },
  {
    tone: "plain",
    title: "Where it failed",
    body: "Counted, not described. Every refused candidate keeps the reason it was refused and the evidence that produced it.",
    verdict: "rejection ledger",
  },
];
