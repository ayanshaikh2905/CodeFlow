import {
  BaseEdge,
  Background,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  getBezierPath,
  useReactFlow,
  type Edge as FlowEdge,
  type EdgeProps,
  type Node as FlowNode,
  type NodeProps,
} from "@xyflow/react";
import dagre from "dagre";
import Prism from "prismjs";
import "prismjs/components/prism-python";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  EdgeKind,
  Graph,
  GraphEdge,
  GraphNode,
  ProjectInfo,
  Route,
  Scenario,
  SourcePayload,
} from "./types";

interface FlowNodeData extends Record<string, unknown> {
  graphNode: GraphNode;
  badges: string[];
  active: boolean;
}

interface FlowEdgeData extends Record<string, unknown> {
  confidence: EdgeKind;
  flowType: GraphEdge["type"];
  ghost: boolean;
  active: boolean;
  mechanism?: string;
}

type CodeFlowNode = FlowNode<FlowNodeData, "codeflow">;
type CodeFlowEdge = FlowEdge<FlowEdgeData, "codeflow">;

const NODE_WIDTH = 236;
const NODE_HEIGHT = 94;
const SPEEDS = [0.5, 1, 1.5, 2];

const nodeTypes = { codeflow: FlowNodeCard };
const edgeTypes = { codeflow: FlowEdgeLine };

function FlowNodeCard({ data, selected }: NodeProps<CodeFlowNode>) {
  const { graphNode } = data;
  return (
    <div className={`flow-node node-${graphNode.kind} ${selected ? "is-selected" : ""} ${data.active ? "is-active" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="node-topline">
        <span className={`kind-dot dot-${graphNode.kind}`} />
        <span className="node-kind">{graphNode.kind}</span>
        {data.badges.map((badge) => <span className={`data-badge badge-${badge.toLowerCase()}`} key={badge}>{badge}</span>)}
      </div>
      <strong className="node-name">{graphNode.name}</strong>
      <span className="node-qualname">{graphNode.qualname}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function FlowEdgeLine({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data }: EdgeProps<CodeFlowEdge>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const className = [
    "flow-edge",
    `edge-${data?.confidence ?? "parsed"}`,
    data?.ghost ? "edge-ghost" : "",
    data?.active ? "edge-active" : "",
  ].join(" ");
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} className={className} />
      {data?.active && <>
        <path id={`motion-${id}`} d={path} fill="none" stroke="transparent" />
        <circle r="5" className="edge-token">
          <animateMotion dur="1.1s" repeatCount="indefinite"><mpath href={`#motion-${id}`} /></animateMotion>
        </circle>
      </>}
      <EdgeLabelRenderer>
        <div className={`edge-label ${data?.ghost ? "ghost-label" : ""}`} style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
          {data?.flowType ?? "calls"}{data?.confidence === "inferred" && <span className="question-badge">?</span>}
          {data?.ghost && data.mechanism && <span className="mechanism-label">{data.mechanism}</span>}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [selectedRoute, setSelectedRoute] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [displayMode, setDisplayMode] = useState<"static" | "observed" | "both">("static");
  const [source, setSource] = useState<SourcePayload | null>(null);
  const [routeSearch, setRouteSearch] = useState("");
  const [playing, setPlaying] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [visibleKinds, setVisibleKinds] = useState<Record<EdgeKind, boolean>>({ parsed: true, inferred: true, observed: true });
  const [error, setError] = useState("");
  const [projectError, setProjectError] = useState("");
  const [projectInput, setProjectInput] = useState("");
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectRevision, setProjectRevision] = useState(0);

  useEffect(() => {
    Promise.all([
      fetch("/api/graph").then(assertResponse).then((response) => response.json() as Promise<Graph>),
      fetch("/api/routes").then(assertResponse).then((response) => response.json() as Promise<Route[]>),
      fetch("/api/project").then(assertResponse).then((response) => response.json() as Promise<ProjectInfo>),
    ]).then(([nextGraph, nextRoutes, nextProject]) => {
      setGraph(nextGraph);
      setRoutes(nextRoutes);
      setProjectInfo(nextProject);
      if (nextRoutes.length) setSelectedRoute(`${nextRoutes[0].method} ${nextRoutes[0].path}`);
    }).catch((reason: unknown) => setError(errorMessage(reason)));
  }, []);

  useEffect(() => {
    if (!selectedRoute) return;
    setPlaying(false);
    setStepIndex(0);
    fetch(`/api/scenario?route=${encodeURIComponent(selectedRoute)}&mode=${displayMode}`)
      .then(assertResponse)
      .then((response) => response.json() as Promise<Scenario>)
      .then((nextScenario) => {
        setScenario(nextScenario);
        setSelectedNodeId(nextScenario.steps[0]?.node_id ?? nextScenario.route.handler_node_id);
      })
      .catch((reason: unknown) => setError(errorMessage(reason)));
  }, [selectedRoute, displayMode, projectRevision]);

  const openProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const location = projectInput.trim();
    if (!location) return;
    setProjectBusy(true);
    setProjectError("");
    try {
      const openResponse = await fetch("/api/project", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location }),
      });
      if (!openResponse.ok) throw new Error(await responseError(openResponse));
      const nextProject = await openResponse.json() as ProjectInfo;
      const [graphResponse, routesResponse] = await Promise.all([
        fetch("/api/graph").then(assertResponse),
        fetch("/api/routes").then(assertResponse),
      ]);
      const [nextGraph, nextRoutes] = await Promise.all([
        graphResponse.json() as Promise<Graph>,
        routesResponse.json() as Promise<Route[]>,
      ]);
      setProjectInfo(nextProject);
      setGraph(nextGraph);
      setRoutes(nextRoutes);
      setScenario(null);
      setSelectedNodeId("");
      setSelectedRoute(nextRoutes.length ? `${nextRoutes[0].method} ${nextRoutes[0].path}` : "");
      setProjectRevision((revision) => revision + 1);
      setProjectPickerOpen(false);
      setProjectInput("");
    } catch (reason: unknown) {
      setProjectError(errorMessage(reason));
    } finally {
      setProjectBusy(false);
    }
  };

  const currentStep = scenario?.steps[stepIndex];
  const scenarioNodeIds = useMemo(() => new Set(scenario?.steps.map((step) => step.node_id) ?? []), [scenario]);

  const visibleGraph = useMemo(() => {
    if (!graph || !scenario) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
    const included = new Set(scenarioNodeIds);
    const candidateEdges = graph.edges.filter((edge) => scenarioNodeIds.has(edge.source));
    for (const edge of candidateEdges) {
      if (["reads", "writes", "http_call"].includes(edge.type)) included.add(edge.target);
    }
    for (const edge of graph.edges) {
      if (included.has(edge.source) && edge.type === "depends_on") included.add(edge.target);
    }
    const edges = graph.edges.filter((edge) => included.has(edge.source) && included.has(edge.target) && edge.type !== "imports");
    const nodeMap = new Map(graph.nodes.map((node) => [node.id, node]));
    const tableReads = new Set(scenario.tables_read);
    const tableWrites = new Set(scenario.tables_written);
    const nodes = [...included].map((id) => nodeMap.get(id)).filter((node): node is GraphNode => Boolean(node));
    return { nodes: nodes.map((node) => ({ ...node, meta: { ...node.meta, read: node.kind === "table" && tableReads.has(node.name), write: node.kind === "table" && tableWrites.has(node.name) } })), edges };
  }, [graph, scenario, scenarioNodeIds]);

  const flowElements = useMemo(() => {
    if (!scenario) return { nodes: [] as CodeFlowNode[], edges: [] as CodeFlowEdge[] };
    const layout = new dagre.graphlib.Graph();
    layout.setDefaultEdgeLabel(() => ({}));
    layout.setGraph({ rankdir: "LR", ranksep: 95, nodesep: 44, marginx: 32, marginy: 36 });
    const nodeIds = new Set(visibleGraph.nodes.map((node) => node.id));
    visibleGraph.nodes.forEach((node) => layout.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
    visibleGraph.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)).forEach((edge) => layout.setEdge(edge.source, edge.target));
    dagre.layout(layout);
    const nodePositions = new Map(scenario.steps.map((step) => [step.node_id, step.order]));
    const nodes = visibleGraph.nodes.map((node) => {
      const point = layout.node(node.id);
      const position = { x: point.x - NODE_WIDTH / 2, y: point.y - NODE_HEIGHT / 2 };
      return {
        id: node.id,
        type: "codeflow" as const,
        position,
        data: {
          graphNode: node,
          badges: [node.meta.read ? "R" : "", node.meta.write ? "W" : ""].filter(Boolean) as string[],
          active: node.id === currentStep?.node_id,
        },
        zIndex: nodePositions.get(node.id) ?? 0,
      };
    });
    const activeEdgeId = currentStep?.edge_id;
    const edges = visibleGraph.edges.filter((edge) => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false;
      const observed = Boolean(edge.meta.observed) || edge.kind === "observed";
      if (displayMode === "static" && edge.kind === "observed") return false;
      if (displayMode === "observed" && !observed && !["reads", "writes", "http_call"].includes(edge.type)) return false;
      const confidence = displayMode === "observed" && observed ? "observed" : edge.kind;
      return visibleKinds[confidence as EdgeKind];
    }).map((edge) => {
      const observed = Boolean(edge.meta.observed) || edge.kind === "observed";
      const confidence = displayMode === "observed" && observed ? "observed" : edge.kind;
      return ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "codeflow" as const,
      markerEnd: { type: MarkerType.ArrowClosed, color: edge.meta.ghost ? "#b88cff" : confidence === "observed" ? "#62d8c9" : confidence === "inferred" ? "#e8b653" : "#7991ad" },
      data: {
        confidence,
        flowType: edge.type,
        ghost: Boolean(edge.meta.ghost),
        active: edge.id === activeEdgeId,
        mechanism: typeof edge.meta.mechanism === "string" ? edge.meta.mechanism : undefined,
      },
    });
    });
    return { nodes, edges };
  }, [visibleGraph, scenario, currentStep, visibleKinds, displayMode]);

  useEffect(() => {
    if (!playing || !scenario) return;
    const timer = window.setInterval(() => {
      setStepIndex((index) => {
        if (index >= scenario.steps.length - 1) {
          setPlaying(false);
          return index;
        }
        return index + 1;
      });
    }, 950 / speed);
    return () => window.clearInterval(timer);
  }, [playing, scenario, speed]);

  useEffect(() => {
    if (!selectedNodeId) return;
    fetch(`/api/source?node_id=${encodeURIComponent(selectedNodeId)}`)
      .then(assertResponse)
      .then((response) => response.json() as Promise<SourcePayload>)
      .then(setSource)
      .catch(() => setSource(null));
  }, [selectedNodeId, graph]);

  const filteredRoutes = routes.filter((route) => `${route.method} ${route.path}`.toLowerCase().includes(routeSearch.toLowerCase()));
  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId);
  const codeLines = source?.code?.split("\n") ?? [];
  const incoming = source?.incoming ?? [];
  const outgoing = source?.outgoing ?? [];
  const currentObservation = currentStep?.observation ?? (displayMode === "both"
    ? [...(scenario?.observed_steps ?? [])].reverse().find((step) => step.node_id === currentStep?.node_id)?.observation
    : undefined);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    const step = scenario?.steps.findIndex((item) => item.node_id === nodeId);
    if (step !== undefined && step >= 0) setStepIndex(step);
  }, [scenario]);

  const stepForward = () => {
    if (!scenario) return;
    setStepIndex((index) => Math.min(index + 1, scenario.steps.length - 1));
    setPlaying(false);
  };

  const stepBack = () => {
    setStepIndex((index) => Math.max(index - 1, 0));
    setPlaying(false);
  };

  const toggleKind = (kind: EdgeKind) => setVisibleKinds((current) => ({ ...current, [kind]: !current[kind] }));

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup"><div className="brand-mark"><span /><span /><span /></div><div><strong>CodeFlow</strong><small>Request explorer</small></div></div>
        <div className="topbar-center"><span className="pulse-dot" /> LOCAL ANALYSIS <span className="topbar-divider" /> {routes.length} ROUTES INDEXED</div>
        <div className="topbar-right"><button className="project-open-button" onClick={() => { setProjectError(""); setProjectPickerOpen((open) => !open); }}>Open project</button><button className="icon-button" title="Refresh analysis" onClick={() => window.location.reload()}>↻</button></div>
        {projectPickerOpen && <form className="project-picker" onSubmit={openProject}>
          <label htmlFor="project-location">Open a local folder or GitHub repo</label>
          <input id="project-location" autoFocus value={projectInput} onChange={(event) => setProjectInput(event.target.value)} placeholder={'C:\\path\\to\\project or https://github.com/owner/repo'} />
          <small>GitHub repositories open from their default branch. Your files stay on this computer.</small>
          {projectError && <div className="project-picker-error">{projectError}</div>}
          <div className="project-picker-actions"><button type="button" className="picker-cancel" onClick={() => setProjectPickerOpen(false)}>Cancel</button><button type="submit" className="picker-open" disabled={projectBusy || !projectInput.trim()}>{projectBusy ? "Opening…" : "Analyze project"}</button></div>
        </form>}
      </header>

      <main className="workspace">
        <aside className="route-sidebar">
          <div className="sidebar-heading"><div><span className="eyebrow">SCENARIO</span><h1>Request flows</h1></div><span className="route-count">{routes.length}</span></div>
          <label className="search-box"><span>⌕</span><input value={routeSearch} onChange={(event) => setRouteSearch(event.target.value)} placeholder="Find an endpoint" /><kbd>⌘ K</kbd></label>
          <div className="route-list-label"><span>ENDPOINTS</span><span>METHOD · PATH</span></div>
          <div className="route-list">
            {filteredRoutes.map((route) => {
              const label = `${route.method} ${route.path}`;
              const active = selectedRoute === label;
              return <button key={route.id} className={`route-item ${active ? "route-active" : ""}`} onClick={() => setSelectedRoute(label)}>
                <span className={`method-pill method-${route.method.toLowerCase()}`}>{route.method}</span>
                <span className="route-path">{route.path}</span>
                {active && <span className="route-chevron">›</span>}
              </button>;
            })}
            {!filteredRoutes.length && <div className="empty-routes">No routes match that search.</div>}
          </div>
          <div className="sidebar-footer" title={projectInfo?.location ?? ""}><span className="local-icon">⌂</span><span><strong>{projectInfo?.name ?? "Project"}</strong><small>{projectInfo?.location ?? "Waiting for project"}</small></span><span className="connected-indicator" /></div>
        </aside>

        <section className="flow-workspace">
          <div className="flow-toolbar">
            <div className="scenario-title"><span className="eyebrow">SCENARIO MAP</span><h2>{scenario ? <><span className="title-method">{scenario.route.method}</span> {scenario.route.path}</> : "Loading route…"}</h2></div>
            <div className="graph-actions">
              <div className="mode-switch" aria-label="Graph view mode">
                {(["static", "observed", "both"] as const).map((mode) => <button key={mode} className={displayMode === mode ? "mode-active" : ""} onClick={() => setDisplayMode(mode)}>{mode}</button>)}
              </div>
              <div className="edge-filters" aria-label="Confidence filters">
                {(["parsed", "inferred", "observed"] as EdgeKind[]).map((kind) => <button key={kind} title={`Toggle ${kind} edges`} className={`filter-chip ${visibleKinds[kind] ? "filter-on" : ""}`} onClick={() => toggleKind(kind)}><span className={`filter-mark mark-${kind}`} />{kind}</button>)}
              </div>
              <span className="toolbar-divider" />
              <button className="fit-button" title="Fit graph" onClick={() => window.dispatchEvent(new Event("codeflow-fit"))}>⊡ Fit view</button>
            </div>
          </div>

          <div className="canvas-wrap">
            {error && <div className="error-banner">Couldn’t load CodeFlow data: {error}</div>}
            {displayMode === "observed" && scenario && !scenario.has_observation && <div className="recording-banner">No runtime recording for this route yet. Run <code>codeflow record</code> to add one.</div>}
            {!graph && !error && <div className="canvas-loading"><span className="loading-orbit" />Building your scenario map…</div>}
            {graph && routes.length === 0 && <div className="empty-project-state"><strong>No FastAPI routes found</strong><span>Choose a Python backend folder or GitHub repository. CodeFlow currently detects FastAPI routes and SQLAlchemy models.</span>{Array.isArray(graph.meta.warnings) && graph.meta.warnings.map((warning) => <small key={String(warning)}>{String(warning)}</small>)}</div>}
            {graph && scenario && <ReactFlow
              nodes={flowElements.nodes}
              edges={flowElements.edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
              minZoom={0.25}
              maxZoom={1.5}
              onNodeClick={(_, node) => selectNode(node.id)}
              proOptions={{ hideAttribution: true }}
              className="scenario-flow"
            >
              <Background color="#2b3850" gap={24} size={1} />
              <MiniMap style={{ width: 116, height: 78 }} pannable zoomable maskColor="rgba(8, 14, 25, 0.66)" nodeColor={(node) => {
                const graphNode = (node.data as FlowNodeData).graphNode;
                return graphNode.kind === "route" ? "#65d3c4" : graphNode.kind === "table" ? "#e4ad61" : "#617490";
              }} />
              <Controls showInteractive={false} />
              <FitViewEvent />
            </ReactFlow>}
            <div className="canvas-watermark"><span className="watermark-square" /> SCENARIO SCOPE <span>·</span> {scenario?.steps.length ?? 0} STEPS</div>
          </div>

          <div className="playback-bar">
            <div className="playback-left"><button className="transport-button" onClick={() => { setPlaying(false); setStepIndex(0); }} title="Restart">↺</button><button className="transport-button" onClick={stepBack} title="Previous step">‹</button><button className="play-button" onClick={() => { if (scenario && stepIndex === scenario.steps.length - 1) setStepIndex(0); setPlaying((value) => !value); }} title={playing ? "Pause" : "Play"}>{playing ? "Ⅱ" : "▶"}</button><button className="transport-button" onClick={stepForward} title="Next step">›</button><span className="transport-divider" /><label className="speed-select"><span>Speed</span><select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{SPEEDS.map((value) => <option key={value} value={value}>{value}×</option>)}</select></label></div>
            <div className="timeline"><div className="timeline-label"><span>REQUEST TRAVEL</span><span>{scenario ? String(stepIndex + 1).padStart(2, "0") : "00"} <i>/</i> {String(scenario?.steps.length ?? 0).padStart(2, "0")}</span></div><input type="range" min="0" max={Math.max((scenario?.steps.length ?? 1) - 1, 0)} value={stepIndex} onChange={(event) => { setPlaying(false); setStepIndex(Number(event.target.value)); }} style={{ "--progress": `${scenario && scenario.steps.length > 1 ? (stepIndex / (scenario.steps.length - 1)) * 100 : 0}%` } as React.CSSProperties} /></div>
            <div className="current-step"><span className="step-pulse" /><span>{selectedNode?.name ?? "Waiting"}</span></div>
          </div>
        </section>

        <aside className="details-panel">
          <div className="details-header"><div><span className="eyebrow">INSPECTOR</span><h2>Step details</h2></div><span className="panel-menu">···</span></div>
          {selectedNode && graph ? <>
            <div className="selected-summary"><div className={`summary-icon icon-${selectedNode.kind}`}>{kindGlyph(selectedNode.kind)}</div><div><span className="selected-kind">{selectedNode.kind.toUpperCase()}</span><h3>{selectedNode.name}</h3><p>{selectedNode.qualname}</p></div></div>
            <div className="file-location"><span className="file-icon">▤</span><span>{source?.file ?? selectedNode.file ?? "No source file"}</span>{source?.line_start && <span className="line-range">L{source.line_start}{source.line_end && source.line_end !== source.line_start ? `–${source.line_end}` : ""}</span>}</div>
            <div className="code-section"><div className="section-title"><span>SOURCE</span><span className="code-language">PYTHON</span></div>
              {source?.code ? <div className="code-window"><div className="code-gutter">{codeLines.map((_, index) => <span key={index}>{(source.line_start ?? 1) + index}</span>)}</div><pre><code>{codeLines.map((line, index) => {
                const highlighted = Prism.highlight(line || " ", Prism.languages.python, "python");
                return <span className={`code-line ${currentStep?.node_id === selectedNode.id ? "line-in-scenario" : ""}`} key={index} dangerouslySetInnerHTML={{ __html: highlighted || " " }} />;
              })}</code></pre></div> : <div className="no-source">Source is not available for this node.</div>}
            </div>
            {currentObservation && <div className="observed-values"><div className="section-title"><span>OBSERVED VALUES</span><span className="observed-indicator">● LIVE</span></div>
              {Object.entries(currentObservation.args).map(([name, value]) => <div className="value-row" key={name}><span>{name}<small>{value.type}</small></span><code>{value.repr}</code></div>)}
              {currentObservation.return && <div className="value-row return-value"><span>return<small>{currentObservation.return.type}</small></span><code>{currentObservation.return.repr}</code></div>}
            </div>}
            <div className="connections-section"><div className="section-title"><span>CONNECTIONS</span><span className="connection-count">{incoming.length + outgoing.length}</span></div>
              {incoming.length + outgoing.length ? <div className="connection-list">
                {incoming.map((edge) => <ConnectionRow key={`in-${edge.id}`} edge={edge} direction="in" graph={graph} />)}
                {outgoing.map((edge) => <ConnectionRow key={`out-${edge.id}`} edge={edge} direction="out" graph={graph} />)}
              </div> : <div className="empty-connections">No connected edges were found.</div>}
            </div>
          </> : <div className="inspector-empty">Choose a step in the graph to inspect its source.</div>}
          {scenario && <div className="table-summary"><span className="eyebrow">DATA TOUCHPOINTS</span><div className="table-summary-row"><span>READS</span><div>{scenario.tables_read.length ? scenario.tables_read.map((table) => <span className="table-chip read-chip" key={table}>◉ {table}</span>) : <small>—</small>}</div></div><div className="table-summary-row"><span>WRITES</span><div>{scenario.tables_written.length ? scenario.tables_written.map((table) => <span className="table-chip write-chip" key={table}>↗ {table}</span>) : <small>—</small>}</div></div></div>}
        </aside>
      </main>
    </div>
  );
}

function ConnectionRow({ edge, direction, graph }: { edge: GraphEdge; direction: "in" | "out"; graph: Graph }) {
  const relatedId = direction === "in" ? edge.source : edge.target;
  const relatedNode = graph.nodes.find((node) => node.id === relatedId);
  const confidence = edge.meta.observed ? "observed" : edge.kind;
  return <div className="connection-row"><span className={`connection-arrow ${direction}`}>{direction === "in" ? "↙" : "↗"}</span><div><strong>{relatedNode?.name ?? relatedId}</strong><small>{direction === "in" ? "from" : "to"} · {edge.type.replace("_", " ")}</small></div><span className={`mini-confidence confidence-${confidence}`}>{edge.meta.ghost ? "ghost" : confidence}</span></div>;
}

function FitViewEvent() {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const fit = () => void fitView({ padding: 0.2, duration: 300 });
    window.addEventListener("codeflow-fit", fit);
    return () => window.removeEventListener("codeflow-fit", fit);
  }, [fitView]);
  return null;
}

function kindGlyph(kind: GraphNode["kind"]): string {
  const glyphs: Record<GraphNode["kind"], string> = { route: "↳", function: "ƒ", class: "C", model: "◇", table: "▦", module: "▣", external: "↗" };
  return glyphs[kind];
}

function assertResponse(response: Response): Response {
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response;
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    // Use the HTTP status text when the API response is not JSON.
  }
  return `${response.status} ${response.statusText}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default App;
