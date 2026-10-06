import type { ComponentType, ReactNode, SVGProps } from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * Реестр иллюстраций: собственные нейтральные сюжеты в духе «графа навыков» —
 * точки-узлы, рёбра, орбиты. Дуотон через CSS-токены (hex в tsx запрещены), акцент —
 * розовый бренда (≤ 20 % площади). Инлайн-SVG, aria-hidden, viewBox 240×160 (нет CLS).
 * Никаких логотипов и товарных знаков ФСП.
 */

const ILL = {
  line: 'var(--dark)',
  fill1: 'var(--primary-tint)',
  fill2: 'var(--violet-tint)',
  accent: 'var(--primary)',
  violet: 'var(--violet)',
  danger: 'var(--status-danger)',
  shadow: 'var(--muted)',
  white: 'var(--card)',
} as const;

const SVG_PROPS = {
  viewBox: '0 0 240 160',
  fill: 'none',
  xmlns: 'http://www.w3.org/2000/svg',
} as const;

const STROKE = {
  stroke: ILL.line,
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const;

type SvgProps = SVGProps<SVGSVGElement>;

/** Пустой граф: три слоя точек, ни одного подтверждённого узла. */
function GraphEmpty(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="86" ry="8" fill={ILL.shadow} />
      <g {...STROKE} strokeDasharray="3 4" opacity="0.55">
        <path d="M60 110 L95 70 M95 70 L140 30 M140 30 L180 70 M95 70 L120 110 M140 30 L95 110" />
        <path d="M60 110 L120 110 L180 110 M95 70 L145 70 L180 70" />
      </g>
      {[
        [60, 110],
        [120, 110],
        [180, 110],
        [95, 70],
        [145, 70],
        [180, 70],
        [140, 30],
      ].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="9" fill={ILL.white} {...STROKE} />
      ))}
      <circle cx="140" cy="30" r="9" fill={ILL.fill1} {...STROKE} />
    </svg>
  );
}

/** Путь до категории: ступени-узлы, последний подсвечен. */
function PathEmpty(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="140" rx="90" ry="8" fill={ILL.shadow} />
      <path d="M36 118 C70 118 70 86 104 86 S138 54 172 54 S206 30 214 28" {...STROKE} />
      <circle cx="36" cy="118" r="10" fill={ILL.white} {...STROKE} />
      <circle cx="104" cy="86" r="10" fill={ILL.white} {...STROKE} />
      <circle cx="172" cy="54" r="10" fill={ILL.white} {...STROKE} />
      <circle cx="36" cy="118" r="3.5" fill={ILL.line} />
      <rect x="196" y="12" width="32" height="32" rx="8" fill={ILL.fill1} {...STROKE} />
      <path d="M205 29 l6 6 l12 -13" stroke={ILL.accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Потребность работодателя: лист с текстом и чипы навыков. */
function NeedEmpty(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="88" ry="8" fill={ILL.shadow} />
      <rect x="52" y="22" width="104" height="112" rx="10" fill={ILL.white} {...STROKE} />
      <path d="M68 46 h60 M68 62 h72 M68 78 h48 M68 94 h66" {...STROKE} opacity="0.6" />
      <rect x="150" y="56" width="58" height="22" rx="11" fill={ILL.fill1} {...STROKE} />
      <rect x="162" y="86" width="52" height="22" rx="11" fill={ILL.fill2} {...STROKE} />
      <rect x="146" y="116" width="44" height="22" rx="11" fill={ILL.white} {...STROKE} />
      <circle cx="162" cy="67" r="4" fill={ILL.accent} />
      <circle cx="174" cy="97" r="4" fill={ILL.violet} />
    </svg>
  );
}

/** Общая пустая коробка: ничего нет, но всё впереди. */
function BoxEmpty(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="80" ry="8" fill={ILL.shadow} />
      <path d="M64 70 L120 48 L176 70 L176 124 L120 146 L64 124 Z" fill={ILL.fill2} {...STROKE} />
      <path d="M64 70 L120 92 L176 70 M120 92 L120 146" {...STROKE} />
      <path d="M64 70 L84 54 L140 32 L120 48 Z" fill={ILL.fill1} {...STROKE} />
      <circle cx="186" cy="40" r="6" fill={ILL.accent} />
      <circle cx="46" cy="56" r="4" fill={ILL.violet} />
    </svg>
  );
}

/** Поиск без результата. */
function SearchEmpty(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="80" ry="8" fill={ILL.shadow} />
      <circle cx="108" cy="72" r="38" fill={ILL.fill2} {...STROKE} />
      <circle cx="108" cy="72" r="26" fill={ILL.white} {...STROKE} />
      <path d="M136 100 L166 130" {...STROKE} strokeWidth="6" />
      <path d="M96 66 l24 12 M120 66 l-24 12" {...STROKE} opacity="0.7" />
      <circle cx="176" cy="40" r="5" fill={ILL.accent} />
    </svg>
  );
}

/** Ошибка: разорванное ребро графа. */
function ErrorBroken(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="80" ry="8" fill={ILL.shadow} />
      <circle cx="64" cy="84" r="16" fill={ILL.white} {...STROKE} />
      <circle cx="176" cy="84" r="16" fill={ILL.white} {...STROKE} />
      <path d="M80 84 L108 84 L116 70 L124 98 L132 84 L160 84" {...STROKE} />
      <path d="M112 44 l8 8 M128 44 l-8 8 M120 36 v-12" stroke={ILL.danger} strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="64" cy="84" r="5" fill={ILL.violet} />
      <circle cx="176" cy="84" r="5" fill={ILL.violet} />
    </svg>
  );
}

/** 404: узел, которого нет в графе. */
function Error404(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="80" ry="8" fill={ILL.shadow} />
      <g {...STROKE} opacity="0.6">
        <path d="M56 110 L96 66 L150 66 L186 110 M96 66 L150 110 M150 66 L96 110" />
      </g>
      {[
        [56, 110],
        [96, 66],
        [150, 66],
        [186, 110],
        [96, 110],
        [150, 110],
      ].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="8" fill={ILL.white} {...STROKE} />
      ))}
      <circle cx="123" cy="30" r="12" fill={ILL.fill1} {...STROKE} strokeDasharray="3 3" />
      <text x="123" y="35" textAnchor="middle" fontSize="12" fontWeight="700" fill={ILL.accent}>
        ?
      </text>
    </svg>
  );
}

/** 403: замок на узле. */
function Error403(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="142" rx="80" ry="8" fill={ILL.shadow} />
      <rect x="84" y="72" width="72" height="56" rx="10" fill={ILL.fill2} {...STROKE} />
      <path d="M98 72 V58 a22 22 0 0 1 44 0 V72" {...STROKE} />
      <circle cx="120" cy="98" r="6" fill={ILL.white} {...STROKE} />
      <path d="M120 104 v10" {...STROKE} />
      <circle cx="176" cy="48" r="5" fill={ILL.accent} />
    </svg>
  );
}

/** Герой логина: орбита с узлами вокруг центра. */
function LoginHero(props: SvgProps): ReactNode {
  return (
    <svg {...SVG_PROPS} {...props}>
      <ellipse cx="120" cy="84" rx="92" ry="36" stroke="var(--sidebar-muted)" strokeWidth="1.5" opacity="0.7" transform="rotate(-18 120 84)" />
      <ellipse cx="120" cy="84" rx="60" ry="24" stroke="var(--sidebar-muted)" strokeWidth="1.5" opacity="0.5" transform="rotate(22 120 84)" />
      <circle cx="120" cy="84" r="22" fill={ILL.white} />
      <circle cx="120" cy="84" r="9" fill={ILL.accent} />
      <circle cx="40" cy="60" r="7" fill={ILL.violet} />
      <circle cx="200" cy="110" r="7" fill={ILL.violet} />
      <circle cx="176" cy="46" r="5" fill={ILL.white} />
      <circle cx="64" cy="122" r="5" fill={ILL.white} />
    </svg>
  );
}

const REGISTRY = {
  'graph-empty': GraphEmpty,
  'path-empty': PathEmpty,
  'need-empty': NeedEmpty,
  'box-empty': BoxEmpty,
  'search-empty': SearchEmpty,
  'error-broken': ErrorBroken,
  'error-404': Error404,
  'error-403': Error403,
  'login-hero': LoginHero,
} satisfies Record<string, ComponentType<SvgProps>>;

export type IllustrationName = keyof typeof REGISTRY;

export interface IllustrationProps {
  name: IllustrationName;
  className?: string;
  /** Высота в px; ширина — по пропорции viewBox 240×160. */
  height?: number;
}

export function Illustration({ name, className, height = 160 }: IllustrationProps): ReactNode {
  const Sujet = REGISTRY[name];
  const width = Math.round((height * 240) / 160);
  return (
    <Sujet
      className={cn('shrink-0', className)}
      width={width}
      height={height}
      aria-hidden="true"
      focusable="false"
    />
  );
}
