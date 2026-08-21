type PageHeaderProps = {
  title: string;
};

export function PageHeader({ title }: PageHeaderProps) {
  return (
    <header
      className="flex items-center h-16 px-8 border-b shrink-0"
      style={{
        borderColor: "rgba(23,25,28,0.08)",
        background: "var(--color-white)",
      }}
    >
      <h1 className="text-lg font-semibold" style={{ color: "var(--color-ink)" }}>
        {title}
      </h1>
    </header>
  );
}
