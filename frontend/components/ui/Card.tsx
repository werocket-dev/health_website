export function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-2xl border shadow-sm p-6"
      style={{
        borderColor: "rgba(23,25,28,0.08)",
        background: "var(--color-white)",
        boxShadow: "0 16px 40px -30px rgba(0,0,0,0.35)",
      }}
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide mb-4" style={{ color: "rgba(23,25,28,0.5)" }}>
        {title}
      </h2>
      {children}
    </div>
  );
}
