import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { Toaster } from '@/shared/ui/sonner';
import { isApiError } from '@/shared/api/errors';
import { notifyApiError } from '@/shared/api/feedback';
import { AuthProvider } from '@/shared/auth/AuthContext';
import '@/shared/lib/dayjs';

/**
 * Дерево провайдеров приложения: MotionConfig → react-query → аутентификация →
 * sonner-тосты. Ошибки мутаций показываются тостом глобально; ошибки запросов
 * рендерят экранные состояния (ErrorState с «Повторить»).
 */

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // один повтор только для сетевых/5xx: 401/403/404/409 не изменятся, лишний повтор —
        // секунда скелетона и нагрузка
        retry: (count, error) =>
          count < 1 && !(isApiError(error) && error.status >= 400 && error.status < 500),
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
    mutationCache: new MutationCache({
      // meta.silent — мутация сама показывает ошибку (inline в форме и т. п.)
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silent) return;
        notifyApiError(error);
      },
    }),
  });
}

export function AppProviders({ children }: { children: ReactNode }): ReactNode {
  const [queryClient] = useState(createQueryClient);
  return (
    // MotionConfig reducedMotion="user": motion сам отключает transform-анимации
    // при prefers-reduced-motion, оставляя opacity.
    <MotionConfig reducedMotion="user">
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
      <Toaster />
    </MotionConfig>
  );
}
