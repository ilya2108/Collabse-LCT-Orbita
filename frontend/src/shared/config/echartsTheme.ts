import * as echarts from 'echarts';
import { prefersReducedMotion } from '@/shared/lib/motion';
import { CHART_PALETTE, SURFACE } from './tokens';

/**
 * Тема ECharts 'fsp': категориальная палитра из tokens.ts, рецессивные оси/сетка,
 * фирменный tooltip, анимации с учётом prefers-reduced-motion.
 * Регистрируется один раз; все ReactECharts получают theme={FSP_THEME}.
 */

export const FSP_THEME = 'fsp';

/** = --font-sans globals.css. */
export const CHART_FONT = "'JetBrains Mono Variable', ui-monospace, 'Segoe UI', system-ui, sans-serif";

const axisCommon = {
  axisLine: { lineStyle: { color: SURFACE.border } },
  axisTick: { lineStyle: { color: SURFACE.border } },
  axisLabel: { color: SURFACE.mutedForeground, fontSize: 12 },
  splitLine: { lineStyle: { color: SURFACE.muted } },
  splitArea: { show: false },
} as const;

let registered = false;

/** Идемпотентная регистрация темы. Вызывается из обёрток графиков. */
export function registerFspTheme(): void {
  if (registered) return;
  registered = true;

  // При prefers-reduced-motion графики не анимируются вовсе.
  const reduce = prefersReducedMotion();

  echarts.registerTheme(FSP_THEME, {
    color: [...CHART_PALETTE],
    backgroundColor: 'transparent',
    textStyle: {
      color: SURFACE.foreground,
      fontFamily: CHART_FONT,
    },
    animation: !reduce,
    animationDuration: 600,
    animationEasing: 'cubicOut',
    animationDelay: (i: number) => i * 40,
    animationDurationUpdate: 240,
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    timeAxis: axisCommon,
    logAxis: axisCommon,
    legend: {
      textStyle: { color: SURFACE.mutedForeground, fontSize: 12 },
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
    },
    tooltip: {
      backgroundColor: SURFACE.card,
      borderColor: SURFACE.border,
      borderWidth: 1,
      borderRadius: 8,
      textStyle: { color: SURFACE.foreground, fontSize: 12 },
      extraCssText: 'box-shadow: var(--shadow-overlay, 0 6px 20px rgba(27,28,33,0.12));',
      axisPointer: {
        lineStyle: { color: SURFACE.border },
        crossStyle: { color: SURFACE.border },
      },
    },
    line: {
      lineStyle: { width: 2 },
      symbolSize: 8,
      symbol: 'circle',
      showSymbol: false,
      smooth: false,
    },
    bar: {
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
    graph: {
      color: [...CHART_PALETTE],
    },
  });
}

/** Хелпер для option-уровневых анимационных настроек при перестроении option. */
export function chartAnimation(): {
  animation: boolean;
  animationDuration: number;
  animationEasing: 'cubicOut';
  animationDurationUpdate: number;
} {
  const reduce = prefersReducedMotion();
  return {
    animation: !reduce,
    animationDuration: 600,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 240,
  };
}
