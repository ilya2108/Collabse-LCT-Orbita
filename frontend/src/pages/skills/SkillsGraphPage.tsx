import { useQuery } from '@tanstack/react-query';
import type { EChartsOption } from 'echarts';
import ReactECharts from 'echarts-for-react';
import { LayoutGrid, Orbit } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchMySkillStates } from '@/shared/api/endpoints/candidates';
import { fetchSkillGraph, listSpecializations } from '@/shared/api/endpoints/skills';
import type { CandidateNodeState, SkillEdge, SkillGraph, SkillNode } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { CHART_FONT, FSP_THEME, chartAnimation, registerFspTheme } from '@/shared/config/echartsTheme';
import { nodeStateLabel, specializationLabel } from '@/shared/config/labels';
import { LAYER_COLORS, LAYER_TITLES, SURFACE, type SkillLayer } from '@/shared/config/tokens';
import { cn } from '@/shared/lib/cn';
import { PageHeader } from '@/shared/ui/page-header';
import { SegmentedControl } from '@/shared/ui/segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Switch } from '@/shared/ui/switch';

/**
 * Страница «Граф навыков»: ECharts `graph` по данным GET /skills/graph/{spec}.
 * Слои D/B/C — цветами палитры (dark / primary / violet), рёбра: вертикальные
 * сплошные, боковые пунктиром; тултип — код, название, слой, описание и число связей.
 * Два вида: «Слои» (узлы по трём рядам, детерминированно) и «Силовой» (force).
 * Переключатель специализации — из GET /skills/specializations. Мосты в другие
 * специализации показываются списком под графом (их узлы в этом графе отсутствуют).
 *
 * Для роли candidate добавляется режим «Мой граф» (GET /candidates/me/skill-states,
 * включён по умолчанию, выключаемый): подтверждённые узлы — полная заливка цветом слоя
 * с обводкой primary, затухающие — приглушённая заливка, остальные — серый контур.
 * У работодателя и гостя страница выглядит как раньше, переключателя нет.
 */

type GraphView = 'layers' | 'force';

const LAYER_ORDER: SkillLayer[] = ['C', 'B', 'D'];

function layerOf(node: SkillNode): SkillLayer {
  return (node.layer === 'D' || node.layer === 'B' || node.layer === 'C' ? node.layer : 'B') as SkillLayer;
}

/** Координаты «слоями»: три ряда (C сверху, D снизу), узлы ряда равномерно по ширине. */
function layeredPositions(nodes: SkillNode[]): Map<string, [number, number]> {
  const positions = new Map<string, [number, number]>();
  const byLayer = new Map<SkillLayer, SkillNode[]>();
  for (const node of nodes) {
    const layer = layerOf(node);
    byLayer.set(layer, [...(byLayer.get(layer) ?? []), node]);
  }
  LAYER_ORDER.forEach((layer, row) => {
    const items = byLayer.get(layer) ?? [];
    const width = 1000;
    items.forEach((node, index) => {
      const x = ((index + 1) / (items.length + 1)) * width;
      const y = 100 + row * 220;
      positions.set(node.id, [x, y]);
    });
  });
  return positions;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Опция ECharts. `myStates` — персональные состояния узлов кандидата (режим «Мой граф»);
 * null — общий граф без персональной раскраски. Узел без строки в ответе считается «off».
 */
function buildOption(
  graph: SkillGraph,
  view: GraphView,
  myStates: ReadonlyMap<string, CandidateNodeState> | null,
): EChartsOption {
  const positions = view === 'layers' ? layeredPositions(graph.nodes) : null;
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const layerTitle = (code: SkillLayer): string =>
    graph.layers.find((l) => l.code === code)?.title ?? LAYER_TITLES[code];

  const nodes = graph.nodes.map((node) => {
    const layer = layerOf(node);
    const pos = positions?.get(node.id);
    // Персональное состояние узла: null — общий режим, без раскраски «под кандидата».
    const myState: CandidateNodeState | null = myStates ? (myStates.get(node.id) ?? 'off') : null;
    const dimmed = myState === 'expired' || myState === 'off';
    const itemStyle =
      myState === null
        ? { color: LAYER_COLORS[layer], borderColor: SURFACE.card, borderWidth: 2 }
        : myState === 'fresh'
          ? { color: LAYER_COLORS[layer], borderColor: SURFACE.primary, borderWidth: 3 }
          : myState === 'fading'
            ? { color: LAYER_COLORS[layer], opacity: 0.4, borderColor: SURFACE.card, borderWidth: 2 }
            : { color: SURFACE.muted, borderColor: SURFACE.sidebarMuted, borderWidth: 1.5 };
    return {
      id: node.id,
      name: node.code,
      category: LAYER_ORDER.indexOf(layer),
      x: pos?.[0],
      y: pos?.[1],
      symbolSize: 34 + Math.min(degree.get(node.id) ?? 0, 6) * 3,
      itemStyle,
      label: {
        show: true,
        position: 'bottom' as const,
        formatter: `{code|${node.code}}\n{title|${node.title}}`,
        rich: {
          code: { color: SURFACE.mutedForeground, fontSize: 10, fontWeight: 600, lineHeight: 14 },
          title: {
            color: dimmed ? SURFACE.mutedForeground : SURFACE.foreground,
            fontSize: 11,
            lineHeight: 14,
            width: 124,
          },
        },
      },
      value: node.description,
      meta: {
        title: node.title,
        layer,
        description: node.description,
        links: degree.get(node.id) ?? 0,
        myState,
      },
    };
  });

  const links = graph.edges.map((edge: SkillEdge) => ({
    source: edge.source,
    target: edge.target,
    lineStyle: {
      color: edge.kind === 'lateral' ? SURFACE.mutedForeground : SURFACE.sidebar,
      width: edge.kind === 'lateral' ? 1.2 : 1.8,
      opacity: edge.kind === 'lateral' ? 0.45 : 0.6,
      type: edge.kind === 'lateral' ? ('dashed' as const) : ('solid' as const),
      curveness: view === 'layers' && edge.kind === 'lateral' ? 0.35 : 0,
    },
    meta: { kind: edge.kind },
  }));

  const anim = chartAnimation();

  return {
    ...anim,
    textStyle: { fontFamily: CHART_FONT },
    legend: {
      top: 0,
      left: 0,
      data: LAYER_ORDER.map((code) => ({
        name: `${code} — ${layerTitle(code)}`,
        itemStyle: { color: LAYER_COLORS[code] },
      })),
    },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: unknown) => {
        const p = params as {
          dataType?: string;
          data?: {
            name?: string;
            meta?: {
              title: string;
              layer: SkillLayer;
              description: string;
              links: number;
              kind?: string;
              myState?: CandidateNodeState | null;
            };
          };
        };
        if (p.dataType === 'edge') {
          const kind = p.data?.meta?.kind;
          const label =
            kind === 'lateral' ? 'боковая связь' : kind === 'bridge' ? 'мост' : 'вертикальная связь';
          return `<span style="font-size:12px">${label}</span>`;
        }
        const meta = p.data?.meta;
        if (!meta) return '';
        // Строка «У вас: …» — только в режиме «Мой граф» (персональное состояние узла).
        const mine = meta.myState
          ? `<div style="margin-top:6px;font-weight:600;color:${
              meta.myState === 'fresh' ? SURFACE.primaryDeep : SURFACE.mutedForeground
            }">У вас: ${escapeHtml(nodeStateLabel(meta.myState))}</div>`
          : '';
        return [
          `<div style="max-width:280px;white-space:normal">`,
          `<div style="font-weight:600">${escapeHtml(p.data?.name ?? '')} · ${escapeHtml(meta.title)}</div>`,
          `<div style="color:${SURFACE.mutedForeground};margin:2px 0 6px">Слой ${meta.layer} — ${escapeHtml(layerTitle(meta.layer))} · связей: ${meta.links}</div>`,
          `<div>${escapeHtml(meta.description)}</div>`,
          mine,
          `</div>`,
        ].join('');
      },
    },
    series: [
      {
        type: 'graph',
        layout: view === 'layers' ? 'none' : 'force',
        roam: true,
        zoom: view === 'layers' ? 0.82 : 1,
        draggable: view === 'force',
        force: { repulsion: 420, edgeLength: [90, 160], gravity: 0.08 },
        categories: LAYER_ORDER.map((code) => ({
          name: `${code} — ${layerTitle(code)}`,
          itemStyle: { color: LAYER_COLORS[code] },
        })),
        data: nodes,
        links,
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3, opacity: 1 },
          itemStyle: { borderColor: SURFACE.primary, borderWidth: 3 },
        },
        lineStyle: { curveness: 0 },
        labelLayout: { hideOverlap: false },
        edgeSymbol: ['none', 'none'],
        // Запас под подписи узлов (две строки снизу) и легенду сверху.
        top: 48,
        bottom: 72,
        left: 72,
        right: 72,
      },
    ],
  };
}

export function SkillsGraphPage(): ReactNode {
  const { spec = 'be' } = useParams<{ spec: string }>();
  const navigate = useNavigate();
  const { activeRole } = useAuth();
  const isCandidate = activeRole === 'candidate';
  const [view, setView] = useState<GraphView>('layers');
  // «Мой граф»: для кандидата включён по умолчанию, выключается переключателем.
  const [myView, setMyView] = useState(true);

  registerFspTheme();

  const specsQuery = useQuery({
    queryKey: ['skills', 'specializations'],
    queryFn: ({ signal }) => listSpecializations(signal),
    staleTime: 5 * 60_000,
  });

  const graphQuery = useQuery({
    queryKey: ['skills', 'graph', spec],
    queryFn: ({ signal }) => fetchSkillGraph(spec, signal),
    staleTime: 5 * 60_000,
  });

  // Персональные состояния узлов — только для роли candidate. Любая ошибка
  // (403/404 у чужой роли, нет профиля) обрабатывается молча: общий граф без переключателя.
  const statesQuery = useQuery({
    queryKey: ['candidates', 'me', 'skill-states'],
    queryFn: ({ signal }) => fetchMySkillStates(signal),
    enabled: isCandidate,
    staleTime: 60_000,
    retry: false,
  });

  const myStates = useMemo<ReadonlyMap<string, CandidateNodeState> | null>(() => {
    if (!isCandidate || !myView || !statesQuery.data) return null;
    return new Map(statesQuery.data.states.map((s) => [s.node_id, s.state]));
  }, [isCandidate, myView, statesQuery.data]);

  const option = useMemo(
    () => (graphQuery.data ? buildOption(graphQuery.data, view, myStates) : null),
    [graphQuery.data, view, myStates],
  );

  const graph = graphQuery.data;
  const specs = specsQuery.data ?? [];
  const currentSpec = specs.find((s) => s.code === spec) ?? null;

  const edgeStats = useMemo(() => {
    if (!graph) return null;
    const vertical = graph.edges.filter((e) => e.kind === 'vertical').length;
    const lateral = graph.edges.filter((e) => e.kind === 'lateral').length;
    return { vertical, lateral, bridges: graph.bridges.length };
  }, [graph]);

  return (
    <>
      <PageHeader
        title="Граф навыков"
        subtitle="Три слоя узлов: глубина, база, контекст. Уровень кандидата — число связей между активными узлами"
        meta={
          graph ? (
            <>
              <StatusBadge status={graph.specialization.status === 'active' ? 'success' : 'warning'}>
                {graph.specialization.status === 'active' ? 'Активна' : 'Предпросмотр'}
              </StatusBadge>
              <span className="text-xs text-muted-foreground">
                версия графа {graph.specialization.graph_version} · узлов {graph.nodes.length}
                {edgeStats
                  ? ` · связей ${edgeStats.vertical + edgeStats.lateral} (вертикальных ${edgeStats.vertical}, боковых ${edgeStats.lateral})`
                  : ''}
              </span>
            </>
          ) : null
        }
        extra={
          <>
            {/* «Мой граф» показываем только кандидату и только если состояния удалось получить */}
            {isCandidate && !statesQuery.isError ? (
              <label className="flex cursor-pointer items-center gap-2 text-sm font-medium">
                <Switch
                  checked={myView}
                  onCheckedChange={setMyView}
                  disabled={statesQuery.isPending}
                  aria-label="Мой граф"
                />
                Мой граф
              </label>
            ) : null}
            <SegmentedControl<GraphView>
              label="Вид графа"
              value={view}
              onChange={setView}
              options={[
                { value: 'layers', label: 'Слои', icon: LayoutGrid },
                { value: 'force', label: 'Силовой', icon: Orbit },
              ]}
            />
            <Select value={spec} onValueChange={(next) => navigate(`/skills/graph/${next}`)}>
              <SelectTrigger className="w-[220px]" aria-label="Специализация">
                <SelectValue placeholder={specsQuery.isPending ? 'Загрузка…' : 'Специализация'} />
              </SelectTrigger>
              <SelectContent>
                {/* Спец из URL, которой нет в списке (или список не загрузился) — по словарю подписей */}
                {!currentSpec && !specsQuery.isPending ? (
                  <SelectItem value={spec}>
                    {graph?.specialization.title ?? specializationLabel(spec)}
                  </SelectItem>
                ) : null}
                {specs.map((s) => (
                  <SelectItem key={s.code} value={s.code}>
                    {s.title}
                    {s.status === 'preview' ? ' · предпросмотр' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
      />

      <section className="rounded-lg border border-border bg-card shadow-card">
        {graphQuery.isPending ? (
          <LoadingState rows={8} card={false} className="p-5" />
        ) : graphQuery.isError ? (
          <ErrorState
            error={graphQuery.error}
            title="Не удалось загрузить граф"
            onRetry={() => void graphQuery.refetch()}
          />
        ) : graph && graph.nodes.length === 0 ? (
          <EmptyState
            illustration="graph-empty"
            title="Граф ещё не опубликован"
            description="Для этой специализации узлы пока не заданы. Выберите другую в переключателе выше."
          />
        ) : option ? (
          <div className="p-2">
            <ReactECharts
              option={option}
              theme={FSP_THEME}
              notMerge
              lazyUpdate
              style={{ height: 'clamp(420px, 70vh, 680px)', width: '100%' }}
              opts={{ renderer: 'canvas' }}
            />
            {/* Легенда персональных состояний — только в режиме «Мой граф» */}
            {myStates ? (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pb-1 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Ваши навыки:</span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="size-2.5 rounded-full bg-primary ring-2 ring-primary/30"
                    aria-hidden="true"
                  />
                  {nodeStateLabel('fresh')}
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-full bg-primary opacity-40" aria-hidden="true" />
                  {nodeStateLabel('fading')}
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="size-2.5 rounded-full border border-input bg-muted"
                    aria-hidden="true"
                  />
                  {nodeStateLabel('off')}
                </span>
              </div>
            ) : null}
            <p className="px-3 pb-2 text-xs text-muted-foreground">
              Колесо — масштаб, перетаскивание — панорама. Наведите на узел, чтобы прочитать
              описание и подсветить связи.
            </p>
          </div>
        ) : null}
      </section>

      {graph ? (
        <section className="mt-4 grid gap-4 lg:grid-cols-[1fr_360px]">
          <div className="rounded-lg border border-border bg-card p-5 shadow-card">
            <h2 className="text-base font-semibold">Узлы по слоям</h2>
            <div className="mt-3 grid gap-4 md:grid-cols-3">
              {LAYER_ORDER.map((layer) => {
                const items = graph.nodes.filter((n) => layerOf(n) === layer);
                const title = graph.layers.find((l) => l.code === layer)?.title ?? LAYER_TITLES[layer];
                return (
                  <div key={layer} className="flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          'size-2.5 rounded-full',
                          layer === 'D' && 'bg-layer-d',
                          layer === 'B' && 'bg-layer-b',
                          layer === 'C' && 'bg-layer-c',
                        )}
                        aria-hidden="true"
                      />
                      <span className="text-sm font-semibold">
                        {layer} — {title}
                      </span>
                      <span className="text-xs text-muted-foreground">{items.length}</span>
                    </div>
                    <ul className="space-y-1.5">
                      {items.map((node) => (
                        <li key={node.id} className="text-sm leading-snug">
                          <span className="font-medium text-muted-foreground">{node.code}</span>{' '}
                          <span title={node.description}>{node.title}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5 shadow-card">
            <h2 className="text-base font-semibold">Мосты в другие специализации</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Связи между графами для T/П-профилей и межспециализационных потребностей.
            </p>
            {graph.bridges.length === 0 ? (
              <p className="mt-3 text-sm text-muted-foreground">Мостов нет.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {graph.bridges.map((bridge) => {
                  const source = graph.nodes.find((n) => n.id === bridge.source);
                  const targetSpec = bridge.target.split('.')[0] ?? '';
                  const targetCode = bridge.target.split('.')[1] ?? '';
                  return (
                    <li
                      key={`${bridge.source}-${bridge.target}`}
                      className="flex items-center justify-between gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm"
                    >
                      <span className="min-w-0 truncate">
                        <span className="font-medium">{source?.code ?? bridge.source}</span>
                        {source ? <span className="text-muted-foreground"> · {source.title}</span> : null}
                      </span>
                      <button
                        type="button"
                        onClick={() => navigate(`/skills/graph/${targetSpec}`)}
                        className="shrink-0 rounded-sm bg-primary-tint-2 px-2 py-0.5 text-xs font-medium text-primary-deep hover:underline"
                      >
                        → {specializationLabel(targetSpec)}
                        {targetCode ? ` · ${targetCode}` : ''}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      ) : null}
    </>
  );
}
