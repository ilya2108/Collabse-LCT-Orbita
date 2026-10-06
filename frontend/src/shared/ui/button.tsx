import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * Кнопка shadcn: варианты default (графитовый #1B1C21, белый текст 17.0:1) | brand
 * (залитый синий CTA на --primary-deep #3326D9: белый текст 8.7:1 — AA при любом
 * размере; базовый синий #402FFF и сам даёт 6.8:1, deep оставлен для большего
 * запаса и видимого hover/active) | secondary | outline | ghost |
 * destructive | link (текст primary-deep).
 * Размеры sm(32)|default(36)|lg(40)|icon(36). Нажатие — CSS active:scale-[0.97].
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[color,background-color,border-color,box-shadow,transform] duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 [&_svg]:shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  {
    variants: {
      variant: {
        default: 'bg-dark text-dark-foreground hover:bg-deep active:bg-deep',
        brand:
          'bg-primary-deep text-primary-foreground font-semibold hover:bg-primary-deep-hover active:bg-dark',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-primary-tint-2',
        outline: 'border border-input bg-card hover:bg-accent hover:text-accent-foreground',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-status-danger-deep',
        link: 'text-primary-deep underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-11 rounded-md px-6 text-base',
        icon: 'size-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        ref={ref}
        data-slot="button"
        type={asChild ? type : (type ?? 'button')}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
