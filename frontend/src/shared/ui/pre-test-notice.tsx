import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';

/**
 * Баннер прозрачности перед тестом: честно говорим кандидату, ЧТО фиксируется
 * во время сессии и что содержимое ответов не анализируется.
 *
 * КОНТРАКТ ДЛЯ КОМАНДЫ РАННЕРА: баннер ОБЯЗАТЕЛЕН на экране старта теста —
 * показывайте его ДО кнопки «Начать», вместе с подключением хука
 * `useIntegrityTelemetry` (см. `@/shared/telemetry/useIntegrityTelemetry`).
 * Телеметрия без показанного баннера нарушает принцип прозрачности продукта.
 */
export function PreTestNotice({ className }: { className?: string }): ReactNode {
  return (
    <Alert variant="info" className={className}>
      <Info aria-hidden="true" />
      <AlertTitle>Как устроен контроль честности</AlertTitle>
      <AlertDescription>
        <p>
          Во время теста фиксируются события сессии — вставки из буфера, уходы с вкладки
          и тайминги ответов. Содержимое ваших ответов не анализируется: сохраняются только
          длины и время. Сигналы помогают работодателю доверять результату, а вам — не
          проходить лишние проверки.
        </p>
      </AlertDescription>
    </Alert>
  );
}
