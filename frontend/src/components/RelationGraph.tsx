import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { relationLabel } from "../lib/format";
import type { GraphNode, RelationGraphData, RelationType } from "../lib/types";

/**
 * The relation table drawn as a live node graph.
 *
 * Loaded on demand, so the layout library is a chunk the browser never fetches unless the
 * view is switched on.
 *
 * Drawn on a canvas rather than as SVG. The first version pre-ran the simulation for a fixed
 * number of ticks and froze the result into DOM nodes, which made it feel dead: there was
 * nothing to drag, zooming re-laid-out nothing, and several hundred elements made every
 * interaction stutter. A canvas redraws the whole scene each frame for a fraction of that
 * cost, which is what buys smooth panning, live dragging and a settle you can watch.
 *
 * What it is for stays narrow. Every edge is a row in `relations` that the table beside it
 * shows in full with its evidence. The graph shows shape: which measures several publishers
 * describe, and whether the edges inside such a cluster agree. For a verdict, read the table.
 */

interface SimNode extends GraphNode, SimulationNodeDatum {}

interface SimLink extends SimulationLinkDatum<SimNode> {
  id: number;
  type: RelationType;
  dimension: string;
  severity: number;
  superseded_fact_id: number | null;
}

interface Transform {
  x: number;
  y: number;
  k: number;
}

/** Colours are read from the stylesheet so the canvas follows the theme like everything else. */
interface Palette {
  surface: string;
  text: string;
  muted: string;
  faint: string;
  border: string;
  accent: string;
  edge: Record<string, string>;
  series: string[];
}

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const LABEL_FONT = 11;
// Below this the layout has stopped moving in any way a reader can see.
const SETTLED_ALPHA = 0.02;

export default function RelationGraph({
  data,
  onSelectRelation,
  selectedRelationId,
}: {
  data: RelationGraphData;
  onSelectRelation: (id: number) => void;
  selectedRelationId: number | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const [hovered, setHovered] = useState<SimNode | null>(null);
  const [showLabels, setShowLabels] = useState(true);
  const [settling, setSettling] = useState(true);

  // Everything the render loop touches lives in refs. Putting node positions in state would
  // re-render React sixty times a second to draw pixels it does not own.
  const nodesRef = useRef<SimNode[]>([]);
  const linksRef = useRef<SimLink[]>([]);
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null);
  const viewRef = useRef<Transform>({ x: 0, y: 0, k: 1 });
  const paletteRef = useRef<Palette | null>(null);
  const hoverRef = useRef<SimNode | null>(null);
  const selectedRef = useRef<number | null>(selectedRelationId);
  const labelsRef = useRef(true);
  const dprRef = useRef(1);
  const framedRef = useRef(false);
  const settledRef = useRef(false);
  const fitRef = useRef<((animate?: boolean) => void) | null>(null);

  useEffect(() => {
    selectedRef.current = selectedRelationId;
  }, [selectedRelationId]);
  useEffect(() => {
    labelsRef.current = showLabels;
  }, [showLabels]);

  const documentColour = useMemo(() => {
    const order = data.documents.map((document) => document.id);
    return (id: number) => order.indexOf(id);
  }, [data.documents]);

  /* --- simulation ------------------------------------------------------------------- */

  useEffect(() => {
    const nodes: SimNode[] = data.nodes.map((node) => ({ ...node }));
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const links: SimLink[] = data.edges
      .filter((edge) => byId.has(edge.source) && byId.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        source: byId.get(edge.source) as SimNode,
        target: byId.get(edge.target) as SimNode,
        type: edge.type,
        dimension: edge.dimension,
        severity: edge.severity,
        superseded_fact_id: edge.superseded_fact_id,
      }));

    nodesRef.current = nodes;
    linksRef.current = links;

    const simulation = forceSimulation<SimNode>(nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((node) => node.id)
          .distance((link) => (link.type === "corroborates" ? 70 : 120))
          .strength(0.28),
      )
      // Capped by distance: unbounded charge makes every disconnected cluster push every
      // other one away until the layout is a thin scatter several screens wide. Within that
      // cap it is strong, because a few hundred facts in one component crowd badly otherwise.
      .force("charge", forceManyBody().strength(-320).distanceMax(420))
      // The collision radius includes room for a label, which is most of what stops text
      // from printing over neighbouring nodes.
      .force("collide", forceCollide<SimNode>((node) => radiusOf(node) + 22).strength(0.95))
      .force("x", forceX(0).strength(0.035))
      .force("y", forceY(0).strength(0.05))
      .alpha(1)
      .alphaDecay(0.03);

    simRef.current = simulation;
    // d3 drives its own timer off requestAnimationFrame, which would run beside the render
    // loop below and be throttled independently of it. Measured in a throttled tab that
    // worked out at two ticks a second, so a layout that should settle in four seconds took
    // minutes. Stopped here and advanced from the frame that is drawing anyway, which ties
    // layout progress to painting and gets rid of the second timer.
    simulation.stop();
    framedRef.current = false;
    settledRef.current = false;
    setSettling(true);

    return () => {
      simulation.stop();
      simulation.on("end", null);
    };
  }, [data]);

  /* --- palette, refreshed when the theme changes ------------------------------------ */

  useEffect(() => {
    const read = () => {
      const style = getComputedStyle(document.documentElement);
      const pick = (name: string) => style.getPropertyValue(name).trim() || "#888";
      paletteRef.current = {
        surface: pick("--surface"),
        text: pick("--text"),
        muted: pick("--text-muted"),
        faint: pick("--text-faint"),
        border: pick("--border-strong"),
        accent: pick("--accent-bright"),
        edge: {
          corroborates: pick("--corroborate"),
          contradicts: pick("--contradict"),
          reconciled_by_context: pick("--reconcile"),
          refines: pick("--reconcile"),
          supersedes: pick("--reconcile"),
        },
        series: [1, 2, 3, 4, 5, 6].map((n) => pick(`--series-${n}`)),
      };
    };
    read();

    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", read);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", read);
    };
  }, []);

  /* --- sizing --------------------------------------------------------------------- */

  const resize = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    dprRef.current = dpr;
    const { width, height } = wrap.getBoundingClientRect();
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }, []);

  useEffect(() => {
    resize();
    const wrap = wrapRef.current;
    if (!wrap) return;
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [resize]);

  /* --- fit to the content ---------------------------------------------------------- */

  const fit = useCallback((animate = true) => {
    const canvas = canvasRef.current;
    const nodes = nodesRef.current;
    if (!canvas || !nodes.length) return;
    const dpr = dprRef.current;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    const xs = nodes.map((n) => n.x ?? 0);
    const ys = nodes.map((n) => n.y ?? 0);
    const pad = 70;
    const spanX = Math.max(1, Math.max(...xs) - Math.min(...xs)) + pad * 2;
    const spanY = Math.max(1, Math.max(...ys) - Math.min(...ys)) + pad * 2;
    const k = clamp(Math.min(width / spanX, height / spanY), MIN_ZOOM, 1.6);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const target = { k, x: width / 2 - cx * k, y: height / 2 - cy * k };

    if (!animate) {
      viewRef.current = target;
      return;
    }
    // Eased rather than snapped, so the reader keeps track of where things went.
    const from = { ...viewRef.current };
    const started = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / 420);
      const e = 1 - Math.pow(1 - t, 3);
      viewRef.current = {
        x: from.x + (target.x - from.x) * e,
        y: from.y + (target.y - from.y) * e,
        k: from.k + (target.k - from.k) * e,
      };
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, []);

  useEffect(() => {
    fitRef.current = fit;
  }, [fit]);

  // A rough frame early on so the first paint is not off screen. The considered one happens
  // in the render loop, when the layout has actually stopped moving.
  useEffect(() => {
    const timer = window.setTimeout(() => fit(false), 900);
    return () => window.clearTimeout(timer);
  }, [data, fit]);

  /* --- render loop ------------------------------------------------------------------ */

  useEffect(() => {
    let frame = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      const palette = paletteRef.current;
      if (!canvas || !palette) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const dpr = dprRef.current;
      const width = canvas.width / dpr;
      const height = canvas.height / dpr;
      const view = viewRef.current;
      const hover = hoverRef.current;

      // d3 dispatches "end" from its own timer, which a throttled tab can stretch out
      // indefinitely, so the badge would stick on "settling" long after the layout had
      // stopped moving. Reading alpha from the frame that is drawing anyway is exact and
      // costs nothing.
      const simulation = simRef.current;
      if (simulation) {
        const alpha = simulation.alpha();
        if (alpha > SETTLED_ALPHA) {
          // Several steps per frame while it is still spreading, so the first arrangement
          // arrives quickly, then one at a time so dragging stays responsive.
          const steps = alpha > 0.25 ? 3 : alpha > 0.1 ? 2 : 1;
          for (let i = 0; i < steps; i += 1) simulation.tick();
        }
      }
      const alpha = simulation?.alpha() ?? 0;
      const calm = alpha < SETTLED_ALPHA;
      if (calm !== settledRef.current) {
        settledRef.current = calm;
        setSettling(!calm);
      }
      if (calm && !framedRef.current) {
        framedRef.current = true;
        fitRef.current?.(true);
      }

      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = palette.surface;
      ctx.fillRect(0, 0, width, height);
      ctx.translate(view.x, view.y);
      ctx.scale(view.k, view.k);

      const neighbours = hover ? neighboursOf(hover, linksRef.current) : null;

      // Edges first, so nodes sit on top of their own connections.
      ctx.lineCap = "round";
      for (const link of linksRef.current) {
        const a = link.source as SimNode;
        const b = link.target as SimNode;
        if (a.x == null || b.x == null) continue;
        const dim = neighbours ? !(neighbours.has(a.id) && neighbours.has(b.id)) : false;
        const selected = link.id === selectedRef.current;

        ctx.beginPath();
        ctx.moveTo(a.x, a.y ?? 0);
        // A gentle arc rather than a straight line: two nodes joined by more than one
        // relation would otherwise draw exactly on top of each other.
        const mx = ((a.x ?? 0) + (b.x ?? 0)) / 2;
        const my = ((a.y ?? 0) + (b.y ?? 0)) / 2;
        const dx = (b.x ?? 0) - (a.x ?? 0);
        const dy = (b.y ?? 0) - (a.y ?? 0);
        const bow = 0.08;
        ctx.quadraticCurveTo(mx - dy * bow, my + dx * bow, b.x ?? 0, b.y ?? 0);

        ctx.strokeStyle = palette.edge[link.type] ?? palette.border;
        ctx.globalAlpha = dim ? 0.07 : selected ? 1 : 0.55;
        ctx.lineWidth = (selected ? 3 : link.type === "contradicts" ? 2 : 1.2) / view.k;
        ctx.setLineDash(link.type === "supersedes" ? [6 / view.k, 5 / view.k] : []);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;

      // Nodes.
      for (const node of nodesRef.current) {
        if (node.x == null || node.y == null) continue;
        const dim = neighbours ? !neighbours.has(node.id) : false;
        const radius = radiusOf(node);
        const colour =
          palette.series[documentColour(node.document_id) % palette.series.length] ?? palette.accent;

        ctx.globalAlpha = dim ? 0.12 : 1;

        if (hover?.id === node.id) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, radius + 7 / view.k, 0, Math.PI * 2);
          ctx.fillStyle = colour;
          ctx.globalAlpha = 0.18;
          ctx.fill();
          ctx.globalAlpha = 1;
        }

        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = colour;
        ctx.fill();
        ctx.lineWidth = 1.5 / view.k;
        ctx.strokeStyle = palette.surface;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // Labels last, and only where they fit. Drawn in screen space so they stay legible at
      // every zoom level rather than growing with the graph.
      if (labelsRef.current) {
        ctx.restore();
        ctx.save();
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.font = `${LABEL_FONT}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textBaseline = "middle";

        const placed: Rect[] = [];
        const ranked = [...nodesRef.current].sort(
          (a, b) => b.degree - a.degree || a.id - b.id,
        );

        for (const node of ranked) {
          if (node.x == null || node.y == null) continue;
          const dim = neighbours ? !neighbours.has(node.id) : false;
          if (dim) continue;

          const sx = node.x * view.k + view.x;
          const sy = node.y * view.k + view.y;
          if (sx < -80 || sx > width + 80 || sy < -40 || sy > height + 40) continue;

          const forced = hover?.id === node.id;
          const text = shorten(labelOf(node), forced ? 46 : 26);
          const w = ctx.measureText(text).width;
          const left = sx + radiusOf(node) * view.k + 6;
          const rect: Rect = { x: left, y: sy - 8, w, h: 16 };

          // Greedy by degree: the best-connected node in a crowded area keeps its label and
          // the rest go unlabelled rather than printing on top of one another.
          if (!forced && (placed.some((other) => overlaps(other, rect)) || node.degree < 3)) {
            continue;
          }
          placed.push(rect);

          if (forced) {
            ctx.fillStyle = palette.surface;
            ctx.globalAlpha = 0.92;
            ctx.fillRect(rect.x - 4, rect.y - 2, rect.w + 8, rect.h + 4);
            ctx.globalAlpha = 1;
          }
          ctx.fillStyle = forced ? palette.text : palette.muted;
          ctx.fillText(text, left, sy);
        }
      }
      ctx.restore();
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [documentColour]);

  /* --- interaction ------------------------------------------------------------------ */

  const toGraph = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const box = canvas.getBoundingClientRect();
    const view = viewRef.current;
    return {
      x: (clientX - box.left - view.x) / view.k,
      y: (clientY - box.top - view.y) / view.k,
    };
  }, []);

  const nodeAt = useCallback((gx: number, gy: number) => {
    let best: SimNode | null = null;
    let bestDistance = Infinity;
    for (const node of nodesRef.current) {
      if (node.x == null || node.y == null) continue;
      const d = Math.hypot(node.x - gx, node.y - gy);
      const reach = radiusOf(node) + 6;
      if (d < reach && d < bestDistance) {
        best = node;
        bestDistance = d;
      }
    }
    return best;
  }, []);

  const edgeAt = useCallback((gx: number, gy: number) => {
    let best: SimLink | null = null;
    let bestDistance = 10;
    for (const link of linksRef.current) {
      const a = link.source as SimNode;
      const b = link.target as SimNode;
      if (a.x == null || b.x == null) continue;
      const d = distanceToSegment(gx, gy, a.x, a.y ?? 0, b.x ?? 0, b.y ?? 0);
      if (d < bestDistance) {
        best = link;
        bestDistance = d;
      }
    }
    return best;
  }, []);

  const drag = useRef<{ node: SimNode | null; x: number; y: number; moved: boolean } | null>(null);

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = toGraph(event.clientX, event.clientY);
    const node = nodeAt(x, y);
    drag.current = { node, x: event.clientX, y: event.clientY, moved: false };
    if (node) {
      node.fx = node.x;
      node.fy = node.y;
      simRef.current?.alpha(Math.max(simRef.current.alpha(), 0.3));
      settledRef.current = false;
      setSettling(true);
    }
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current;
    if (!current) {
      const { x, y } = toGraph(event.clientX, event.clientY);
      const node = nodeAt(x, y);
      if (node !== hoverRef.current) {
        hoverRef.current = node;
        setHovered(node);
      }
      return;
    }

    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    if (!current.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    current.moved = true;
    current.x = event.clientX;
    current.y = event.clientY;

    if (current.node) {
      const { x, y } = toGraph(event.clientX, event.clientY);
      current.node.fx = x;
      current.node.fy = y;
    } else {
      viewRef.current = {
        ...viewRef.current,
        x: viewRef.current.x + dx,
        y: viewRef.current.y + dy,
      };
    }
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current;
    drag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (!current) return;

    // Released where it was dropped: a reader who arranges the graph expects it to stay
    // arranged. Double-click frees everything again.
    if (current.moved) return;

    const { x, y } = toGraph(event.clientX, event.clientY);
    if (current.node) return;
    const edge = edgeAt(x, y);
    if (edge) onSelectRelation(edge.id);
  };

  const onDoubleClick = () => {
    for (const node of nodesRef.current) {
      node.fx = null;
      node.fy = null;
    }
    simRef.current?.alpha(0.6);
    settledRef.current = false;
    framedRef.current = false;
    setSettling(true);
  };

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    zoomAbout(event.clientX, event.clientY, event.deltaY < 0 ? 1.14 : 1 / 1.14);
  };

  const zoomAbout = useCallback((clientX: number, clientY: number, factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    const view = viewRef.current;
    const k = clamp(view.k * factor, MIN_ZOOM, MAX_ZOOM);
    const px = clientX - box.left;
    const py = clientY - box.top;
    // Keep the point under the cursor fixed, which is what makes zoom feel controlled.
    viewRef.current = {
      k,
      x: px - ((px - view.x) / view.k) * k,
      y: py - ((py - view.y) / view.k) * k,
    };
  }, []);

  const zoomCentre = (factor: number) => {
    const box = canvasRef.current?.getBoundingClientRect();
    if (!box) return;
    zoomAbout(box.left + box.width / 2, box.top + box.height / 2, factor);
  };

  return (
    <div className="graph">
      <div className="graph__toolbar">
        <div className="graph__legend">
          {(
            ["corroborates", "contradicts", "reconciled_by_context", "refines", "supersedes"] as const
          ).map((type) => (
            <span key={type} className="graph__legend-item">
              <span className={`graph__key graph__key--${type}`} aria-hidden="true" />
              {relationLabel(type)}
            </span>
          ))}
        </div>

        <div className="graph__legend">
          {data.documents.map((document, index) => (
            <span key={document.id} className="graph__legend-item" title={document.title}>
              <span
                className="graph__swatch"
                style={{ background: `var(--series-${(index % 6) + 1})` }}
                aria-hidden="true"
              />
              {shorten(document.title, 26)}
            </span>
          ))}
        </div>

        <div className="graph__controls">
          <button
            type="button"
            className={showLabels ? "btn btn--sm is-active" : "btn btn--sm"}
            onClick={() => setShowLabels((value) => !value)}
            aria-pressed={showLabels}
          >
            Labels
          </button>
          <button type="button" className="btn btn--sm" onClick={() => zoomCentre(1 / 1.25)}>
            &minus;
          </button>
          <button type="button" className="btn btn--sm" onClick={() => zoomCentre(1.25)}>
            +
          </button>
          <button type="button" className="btn btn--sm" onClick={() => fit()}>
            Fit
          </button>
        </div>
      </div>

      <div className="graph__stage" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className="graph__canvas"
          role="img"
          aria-label={`${data.nodes.length} facts joined by ${data.edges.length} relations`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={() => {
            hoverRef.current = null;
            setHovered(null);
          }}
          onDoubleClick={onDoubleClick}
          onWheel={onWheel}
        />
        {settling ? <span className="graph__status">settling</span> : null}
        {hovered ? (
          <div className="graph__tip">
            <strong>{labelOf(hovered)}</strong>
            <span>
              {hovered.value_text ?? "no value"}
              {hovered.period ? ` · ${hovered.period}` : ""}
            </span>
            <span className="meta">
              {hovered.degree} relation{hovered.degree === 1 ? "" : "s"}
            </span>
          </div>
        ) : null}
      </div>

      <p className="meta graph__footnote">
        Drag a node to move it, drag the background to pan, ctrl or cmd with the wheel to zoom,
        double-click to release everything. Click an edge to read the pair and its evidence.{" "}
        {data.truncated
          ? `Showing the ${data.edges.length} highest-severity of ${data.total_relations} matching relations.`
          : `${data.nodes.length} facts, ${data.edges.length} relations. Facts nothing links to are not drawn.`}
      </p>
    </div>
  );
}

/* --- helpers ------------------------------------------------------------------------ */

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function overlaps(a: Rect, b: Rect): boolean {
  return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
}

function radiusOf(node: GraphNode): number {
  return Math.min(13, 4.5 + Math.sqrt(node.degree) * 2.1);
}

function labelOf(node: GraphNode): string {
  const base = node.measure ?? node.label;
  return node.period ? `${base} · ${node.period}` : base;
}

function shorten(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

function neighboursOf(node: SimNode, links: SimLink[]): Set<number> {
  const ids = new Set<number>([node.id]);
  for (const link of links) {
    const a = (link.source as SimNode).id;
    const b = (link.target as SimNode).id;
    if (a === node.id) ids.add(b);
    if (b === node.id) ids.add(a);
  }
  return ids;
}

function distanceToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(px - ax, py - ay);
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / lengthSquared, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
