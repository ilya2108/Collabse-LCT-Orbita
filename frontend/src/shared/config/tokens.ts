/**
 * Единый источник фирменных цветов в виде hex-строк.
 * CSS-слой берёт те же значения из токенов globals.css (OKLCH);
 * hex нужны там, где требуется строка: ECharts, canvas, инлайн-SVG.
 * В компонентах hex запрещены — только tailwind-токены.
 *
 * Палитра брендбука ФСП (fsp-russia.ru/press/brandbook): синий #402FFF,
 * красный #EC1D35, светло-серый #EDEDED, серый #C8C9CA, текст #1B1C21,
 * белый #FFFFFF. Контрастные ограничения — в шапке globals.css.
 */

/** Цвета брендбука и производные роли как есть. */
export const BRAND = {
  primary: '#402FFF',
  primaryHover: '#5A49FF',
  dark: '#1B1C21',
  deep: '#2A2B33',
  violet: '#8F94E3',
  tint: '#DEDAFF',
  text: '#1B1C21',
  white: '#FFFFFF',
} as const;

export type SemanticStatus = 'draft' | 'progress' | 'success' | 'warning' | 'danger' | 'brand';

export interface StatusTriple {
  /** Маркеры, полосы, графики. Текстом НЕ использовать (< 4.5:1 на белом). */
  core: string;
  /** Текст статусного цвета. AA ≥ 4.5 на tint. */
  deep: string;
  /** Фон бейджей/плашек. */
  tint: string;
}

/** Статусные тройки core/deep/tint — совпадают с токенами globals.css. */
export const STATUS_TRIPLES: Record<SemanticStatus, StatusTriple> = {
  draft: { core: '#5A5B66', deep: '#44454E', tint: '#F1F1F5' },
  progress: { core: '#8F94E3', deep: '#4040A8', tint: '#EEEFFA' },
  success: { core: '#09AD4D', deep: '#067A37', tint: '#E7FAEF' },
  warning: { core: '#D78D0E', deep: '#98640A', tint: '#FFF6E7' },
  danger: { core: '#EC1D35', deep: '#B00E22', tint: '#FDE9EB' },
  brand: { core: '#402FFF', deep: '#3326D9', tint: '#DEDAFF' },
} as const;

/** Слои графа навыков (PLAN.md §3): D — глубина, B — база, C — контекст. */
export type SkillLayer = 'D' | 'B' | 'C';

export const LAYER_COLORS: Record<SkillLayer, string> = {
  D: BRAND.dark,
  B: BRAND.primary,
  C: BRAND.violet,
} as const;

/** Подписи слоёв для легенды графа (fallback, если API не отдал layers). */
export const LAYER_TITLES: Record<SkillLayer, string> = {
  D: 'Глубина',
  B: 'База',
  C: 'Контекст',
} as const;

/** Категориальная палитра графиков: фиксированный порядок, не циклится. */
export const CHART_PALETTE = [
  BRAND.primary,
  BRAND.dark,
  BRAND.violet,
  '#148190', // бирюза
  '#B1740B', // охра
  '#5A5B66', // графитовый серый
] as const;

/** Поверхности/текст (hex для ECharts, canvas, SVG). */
export const SURFACE = {
  background: '#F8F8FB',
  card: '#FFFFFF',
  foreground: BRAND.text,
  mutedForeground: '#5A5B66',
  muted: '#F1F1F5',
  border: '#E4E4EA',
  primary: BRAND.primary,
  primaryTint: '#EFEDFF',
  primaryDeep: '#3326D9',
  primaryDeepHover: '#2A1FB8',
  input: '#83848C',
  primaryTint2: BRAND.tint,
  sidebar: BRAND.dark,
  sidebarMuted: '#C8C9CA',
} as const;

/**
 * Тинт-фон произвольного цвета (например, цвета слоя) — вычисляется в рантайме,
 * чистым текстом цвет никогда не идёт.
 */
export function colorTint(color: string): string {
  return `color-mix(in oklab, ${color} 12%, white)`;
}

/** Текст на тинт-фоне произвольного цвета: ≥ 4.5:1 для всех цветов палитры. */
export function colorText(color: string): string {
  return `color-mix(in oklab, ${color} 72%, black)`;
}
