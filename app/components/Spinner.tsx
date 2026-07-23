export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-6" role="status">
      <div className="w-10 h-10 rounded-full border-4 border-moo-ink/15 border-t-moo-accent animate-spin" />
      {label && <p className="text-moo-ink/70 text-sm">{label}</p>}
    </div>
  );
}
