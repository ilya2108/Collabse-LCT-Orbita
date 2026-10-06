import { LogIn, UserPlus } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { AuthErrorScreen, AuthSplash } from '@/app/router/guards';
import { useAuth } from '@/shared/auth/AuthContext';
import { Illustration } from '@/shared/illustrations';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';

/**
 * Экран входа: сплит-лейаут — слева тёмная панель с орбитой и тезисом продукта,
 * справа карточка с кнопкой «Войти через ФСП ID / email» (Keycloak, OIDC + PKCE)
 * и ссылкой «Регистрация» (страница регистрации Keycloak, подтверждение email
 * через письмо). После логина возвращаемся на исходный deep-link.
 */
export function LoginPage(): ReactNode {
  const { phase, error, login, register, retry } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/';
  useDocumentTitle('Вход');

  if (phase === 'initializing') return <AuthSplash />;
  if (phase === 'ready') return <Navigate to={from} replace />;
  if (phase === 'error') {
    return (
      <AuthErrorScreen title="Не удалось подключиться к сервису входа" error={error} onRetry={retry} />
    );
  }

  return (
    <div className="flex min-h-screen bg-background">
      <motion.section
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
        className="hidden w-[44%] max-w-[560px] flex-col items-start justify-center gap-6 bg-sidebar px-12 lg:flex"
      >
        <motion.img
          variants={staggerItem}
          src="/brand/fsp-logo-light.svg"
          alt="Федерация спортивного программирования"
          className="h-9 w-auto"
        />
        <motion.div variants={staggerItem}>
          <Illustration name="login-hero" height={220} />
        </motion.div>
        <motion.h2 variants={staggerItem} className="text-2xl font-semibold text-white">
          ФСП.Орбита
        </motion.h2>
        <motion.p variants={staggerItem} className="max-w-sm text-sm leading-relaxed text-sidebar-muted">
          Граф подтверждённых навыков вместо резюме. Кандидат проходит адаптивный тест и получает
          категорию; работодатель описывает потребность и первым выходит на контакт с зарплатой
          «от–до».
        </motion.p>
      </motion.section>

      <div className="flex min-w-0 flex-1 items-center justify-center px-4">
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="flex w-full max-w-[380px] flex-col items-center gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-card"
        >
          <motion.img
            variants={staggerItem}
            src="/brand/fsp-logo-dark.svg"
            alt="Федерация спортивного программирования"
            className="h-12 w-auto"
          />
          <motion.div variants={staggerItem} className="flex flex-col gap-1">
            <h1 className="text-xl font-semibold">Вход</h1>
            <p className="text-sm text-muted-foreground">
              Один аккаунт для кандидата и работодателя
            </p>
          </motion.div>
          <motion.div variants={staggerItem} className="flex w-full flex-col gap-2">
            <Button variant="brand" size="lg" className="w-full" onClick={() => login(from)}>
              <LogIn aria-hidden="true" />
              Войти через ФСП ID / email
            </Button>
            <Button variant="outline" size="lg" className="w-full" onClick={() => register(from)}>
              <UserPlus aria-hidden="true" />
              Регистрация
            </Button>
          </motion.div>
          <motion.p variants={staggerItem} className="text-xs leading-relaxed text-muted-foreground">
            После регистрации подтвердите адрес по письму. На демо-стенде письма приходят в
            «Почту стенда».
          </motion.p>
        </motion.div>
      </div>
    </div>
  );
}
