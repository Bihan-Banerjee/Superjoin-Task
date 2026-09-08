import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import type { SimulationLinkDatum, SimulationNodeDatum } from "d3-force";
import { useEffect, useMemo, useRef, useState } from "react";

import { relationLabel } from "../lib/format";
import type { GraphEdge, GraphNode, RelationGraphData, RelationType } from "../lib/types";

/**
 * The relation table drawn as a node graph.
 *
 * This module is loaded on demand and only exists when ENABLE_GRAPH_VIEW is on, so the
 * layout library is a separate chunk the browser never fetches otherwise.
 *
 * What it is for is narrow and worth stating, because a picture of a knowledge layer is
 * easily mistaken for the knowledge layer. Every edge here is a row in `relations` that the
 * table beside it shows in full, with its evidence and its reasoning. The one thing the
 * table cannot show is shape: which measures several publishers all describe, and whether
 * the edges inside one of those clusters agree with each other. That is what to read this
 * for. For a verdict, read the table.
 *
 * The simulation is run to completion before the first paint rather than animated. A layout
 * that drifts into place invites watching it instead of reading it, and settled positions
 * are deterministic for the same input, which keeps a screenshot reproducible.
 */

const WIDTH = 1000;
const HEIGHT = 620;
const SETTLE_TICKS = 320;
const MAX_LABELS = 12;
// Movement in CSS pixels before a press counts as a pan rather than a click.
const DRAG_THRESHOLD = 4;
const LABEL_CHARS = 30;

/** Node colours per document, from the shared palette. Cycles beyond six documents. */
const DOCUMENT_COLOURS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
];

interface Placed extends GraphNode {
  x: number;
  y: number;
}

/** d3 attaches its own coordinates and velocities to whatever it is given. */
interface SimNode extends GraphNode, SimulationNodeDatum {}

interface SimLink extends SimulationLinkDatum<SimNode> {
  type: RelationType;
}

export default function RelationGraph({
  data,
  onSelectRelation,
  selectedRelationId,
}: {
  data: RelationGraphData;
  onSelectRelation: (id: number) => void;
  selectedRelationId: number | null;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const pointer = useRef<{ x: number; y: number; panning: boolean } | null>(null);
  // Set while a drag is ending, so the click that follows a pan does not also select
  // whatever edge happened to be under the cursor when the mouse came up.
  const suppressClick = useRef(false);

  const { nodes, edges, extent } = useLayout(data);

  const documentColour = useMemo(() => {
    const map = new Map<number, string>();
    data.documents.forEach((document, index) => {
      map.set(document.id, DOCUMENT_COLOURS[index % DOCUMENT_COLOURS.length] as string);
    });
    return map;
  }, [data.documents]);

  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  // A fixed number of labels rather than a degree threshold. A threshold is the wrong knob:
  // on one corpus it labels a single node and on the next it labels eighty of them on top of
  // each other. Capping the count keeps the graph legible whatever it is pointed at, and the
  // rest are a hover away. Ties break on id so the same corpus always labels the same nodes.
  const labelled = useMemo(() => {
    const ranked = [...nodes].sort((a, b) => b.degree - a.degree || a.id - b.id);
    return new Set(ranked.slice(0, MAX_LABELS).map((node) => node.id));
  }, [nodes]);

  // Hovering a node dims everything it is not connected to. With a few hundred edges this is
  // the difference between a shape and something readable.
  const connected = useMemo(() => {
    if (hovered === null) return null;
    const ids = new Set<number>([hovered]);
    for (const edge of edges) {
      if (edge.source === hovered) ids.add(edge.target);
      if (edge.target === hovered) ids.add(edge.source);
    }
    return ids;
  }, [hovered, edges]);

  // Zoom takes a modifier so a plain wheel still scrolls the page. A 620px canvas that
  // swallows the wheel traps the reader above the relation card the graph exists to open.
  const onWheel = (event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    setView((current) => ({
      ...current,
      scale: Math.min(4, Math.max(0.3, current.scale * (event.deltaY < 0 ? 1.12 : 0.89))),
    }));
  };

  return (
    <div className="graph">
      <div className="graph__toolbar">
        <div className="graph__legend">
          {(["corroborates", "contradicts", "reconciled_by_context", "refines", "supersedes"] as const).map(
            (type) => (
              <span key={type} className="graph__legend-item">
                <svg width="22" height="8" aria-hidden="true">
                  <line
                    x1="1"
                    y1="4"
                    x2="21"
                    y2="4"
                    className={`graph__edge graph__edge--${type}`}
                    strokeDasharray={type === "supersedes" ? "4 3" : undefined}
                  />
                </svg>
                {relationLabel(type)}
              </span>
            ),
          )}
        </div>
        <div className="graph__legend">
          {data.documents.map((document) => (
            <span key={document.id} className="graph__legend-item" title={document.title}>
              <span
                className="graph__swatch"
                style={{ background: documentColour.get(document.id) }}
                aria-hidden="true"
              />
              {document.title.length > 34 ? `${document.title.slice(0, 34)}…` : document.title}
            </span>
          ))}
        </div>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => setView({ x: 0, y: 0, scale: 1 })}
        >
          Reset view
        </button>
      </div>

      <svg
        className="graph__canvas"
        viewBox={`${extent.x} ${extent.y} ${extent.width} ${extent.height}`}
        role="img"
        aria-label={`${nodes.length} facts joined by ${edges.length} relations`}
        onWheel={onWheel}
        onPointerDown={(event) => {
          // Capture is claimed only once a drag is under way. Claiming it here would
          // redirect every later pointer event to the svg, and the edges underneath would
          // stop being clickable at all.
          pointer.current = { x: event.clientX, y: event.clientY, panning: false };
        }}
        onPointerMove={(event) => {
          const start = pointer.current;
          if (!start) return;
          const dx = event.clientX - start.x;
          const dy = event.clientY - start.y;
          if (!start.panning && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
          if (!start.panning) {
            start.panning = true;
            event.currentTarget.setPointerCapture(event.pointerId);
          }
          pointer.current = { x: event.clientX, y: event.clientY, panning: true };
          setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
        }}
        onPointerUp={(event) => {
          if (pointer.current?.panning) {
            suppressClick.current = true;
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
          pointer.current = null;
        }}
      >
        <defs>
          <marker
            id="graph-arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L8,4 L0,8 z" className="graph__arrow" />
          </marker>
        </defs>

        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {edges.map((edge) => {
            const source = byId.get(edge.source);
            const target = byId.get(edge.target);
            if (!source || !target) return null;
            const dimmed = connected !== null && !(connected.has(edge.source) && connected.has(edge.target));
            // Supersession points away from the figure that was replaced.
            const reversed = edge.superseded_fact_id === edge.target;
            return (
              <line
                key={edge.id}
                x1={reversed ? target.x : source.x}
                y1={reversed ? target.y : source.y}
                x2={reversed ? source.x : target.x}
                y2={reversed ? source.y : target.y}
                className={[
                  "graph__edge",
                  `graph__edge--${edge.type}`,
                  edge.id === selectedRelationId ? "graph__edge--selected" : "",
                  dimmed ? "graph__edge--dimmed" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                strokeDasharray={edge.type === "supersedes" ? "5 4" : undefined}
                markerEnd={edge.type === "supersedes" ? "url(#graph-arrow)" : undefined}
                onClick={() => {
                  if (suppressClick.current) {
                    suppressClick.current = false;
                    return;
                  }
                  onSelectRelation(edge.id);
                }}
              >
                <title>
                  {relationLabel(edge.type)}
                  {edge.dimension !== "none" ? ` · ${edge.dimension.replace("_", " ")}` : ""}
                </title>
              </line>
            );
          })}

          {nodes.map((node) => {
            const dimmed = connected !== null && !connected.has(node.id);
            const radius = Math.min(11, 4 + Math.sqrt(node.degree) * 1.9);
            return (
              <g
                key={node.id}
                className={dimmed ? "graph__node graph__node--dimmed" : "graph__node"}
                onPointerEnter={() => setHovered(node.id)}
                onPointerLeave={() => setHovered(null)}
              >
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={radius}
                  style={{ fill: documentColour.get(node.document_id) }}
                />
                {labelled.has(node.id) || hovered === node.id ? (
                  <text x={node.x + radius + 3} y={node.y + 3} className="graph__label">
                    {shorten(
                      `${node.measure ?? node.label}${node.period ? ` · ${node.period}` : ""}`,
                    )}
                  </text>
                ) : null}
                <title>
                  {`${node.measure ?? node.label}${node.period ? ` · ${node.period}` : ""}${
                    node.value_text ? ` = ${node.value_text}` : ""
                  }`}
                </title>
              </g>
            );
          })}
        </g>
      </svg>

      <p className="meta graph__footnote">
        Drag to pan, ctrl or cmd with the wheel to zoom.{" "}
        {data.truncated
          ? `Showing the ${edges.length} highest-severity of ${data.total_relations} matching relations. Filter to see the rest.`
          : `${nodes.length} facts, ${edges.length} relations. Facts nothing links to are not drawn.`}{" "}
        Click an edge to read the pair and its evidence.
      </p>
    </div>
  );
}

function shorten(text: string): string {
  return text.length > LABEL_CHARS ? `${text.slice(0, LABEL_CHARS - 1)}…` : text;
}

/** Runs the force layout to a standstill and returns settled positions plus a viewBox. */
function useLayout(data: RelationGraphData) {
  const [layout, setLayout] = useState<{
    nodes: Placed[];
    edges: GraphEdge[];
    extent: { x: number; y: number; width: number; height: number };
  }>({ nodes: [], edges: [], extent: { x: 0, y: 0, width: WIDTH, height: HEIGHT } });

  useEffect(() => {
    if (!data.nodes.length) {
      setLayout({ nodes: [], edges: [], extent: { x: 0, y: 0, width: WIDTH, height: HEIGHT } });
      return;
    }

    // d3 mutates what it is given, so the simulation runs over copies and the props stay
    // the immutable query result React is holding.
    const nodes: SimNode[] = data.nodes.map((node) => ({ ...node }));
    const links: SimLink[] = data.edges.map((edge) => ({ ...edge }));

    const simulation = forceSimulation<SimNode>(nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((node) => node.id)
          // Corroboration pulls tighter than a reconciliation, so agreeing facts cluster.
          .distance((link) => (link.type === "corroborates" ? 40 : 70))
          .strength(0.35),
      )
      // Repulsion is capped by distance on purpose. A corpus this size is mostly small
      // disconnected clusters, and unbounded charge makes every cluster push every other
      // one away until the layout is a thin scatter across a canvas several screens wide.
      // Limiting the range keeps repulsion doing the job it is for — separating nodes that
      // are actually near each other — and lets the centring forces pack the clusters.
      .force("charge", forceManyBody().strength(-120).distanceMax(240))
      .force("collide", forceCollide(15))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force("x", forceX(WIDTH / 2).strength(0.09))
      .force("y", forceY(HEIGHT / 2).strength(0.12))
      .stop();

    simulation.tick(SETTLE_TICKS);

    const placed: Placed[] = nodes.map((node) => ({ ...node, x: node.x ?? 0, y: node.y ?? 0 }));
    const padding = 60;
    const xs = placed.map((node) => node.x);
    const ys = placed.map((node) => node.y);
    const minX = Math.min(...xs) - padding;
    const minY = Math.min(...ys) - padding;

    setLayout({
      nodes: placed,
      edges: data.edges,
      extent: {
        x: minX,
        y: minY,
        width: Math.max(200, Math.max(...xs) + padding - minX),
        height: Math.max(200, Math.max(...ys) + padding - minY),
      },
    });
  }, [data]);

  return layout;
}
