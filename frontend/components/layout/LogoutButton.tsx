"use client";

export function LogoutButton() {
  return (
    <button
      onClick={() => {
        fetch("/api/logout", { method: "POST" }).finally(() => {
          window.location.href = "/login";
        });
      }}
      className="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
      style={{ color: "rgba(23,25,28,0.55)" }}
    >
      Déconnexion
    </button>
  );
}
