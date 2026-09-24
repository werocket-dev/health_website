"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  label: string;
  href: string;
};

type PageHeaderProps = {
  title: string;
  nav?: NavItem[];
};

export function PageHeader({ title, nav }: PageHeaderProps) {
  const pathname = usePathname();

  return (
    <header
      className="flex items-center justify-between h-16 px-8 border-b shrink-0"
      style={{
        borderColor: "rgba(23,25,28,0.08)",
        background: "var(--color-white)",
      }}
    >
      <h1 className="text-lg font-semibold" style={{ color: "var(--color-ink)" }}>
        {title}
      </h1>
      {nav && nav.length > 0 && (
        <nav className="flex items-center gap-1">
          {nav.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
                style={{
                  background: active ? "rgba(23,25,28,0.08)" : "transparent",
                  color: active ? "var(--color-ink)" : "rgba(23,25,28,0.55)",
                }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      )}
    </header>
  );
}
