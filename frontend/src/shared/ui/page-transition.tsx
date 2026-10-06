import { AnimatePresence, motion } from 'motion/react';
import type { ReactNode } from 'react';
import { useLocation, useOutlet } from 'react-router-dom';
import { motionTokens } from '@/shared/lib/motion';

/**
 * Вход страницы в AppLayout (key = pathname):
 * initial {opacity 0, y: 8} → {1, 0} duration.normal easing.smooth;
 * exit {opacity 0, y: -8} duration.fast. AnimatePresence mode="wait".
 * reduced-motion гасится MotionConfig reducedMotion="user" в корне.
 *
 * Снимок outlet берётся здесь через `useOutlet()`, а не `<Outlet/>` в children: `<Outlet/>`
 * читает живой RouteContext и в уходящем узле показал бы уже НОВЫЙ маршрут (мерцание
 * и двойной mount страницы). Элемент-снимок остаётся прежним маршрутом до конца exit.
 */
export function PageTransition(): ReactNode {
  const { pathname } = useLocation();
  const outlet = useOutlet();
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: motionTokens.distance.sm }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -motionTokens.distance.sm }}
        transition={{ duration: motionTokens.duration.normal, ease: motionTokens.easing.smooth }}
        style={{ minHeight: '100%' }}
      >
        {outlet}
      </motion.div>
    </AnimatePresence>
  );
}
