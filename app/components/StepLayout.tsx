import Link from "next/link";

interface Props {
  step?: number;
  totalSteps?: number;
  title: string;
  subtitle?: string;
  back?: string;
  children: React.ReactNode;
}

export function StepLayout({ step, totalSteps, title, subtitle, back, children }: Props) {
  return (
    <main className="min-h-dvh max-w-md mx-auto px-6 py-8 flex flex-col">
      <header className="mb-8">
        {back && (
          <Link href={back} className="text-moo-ink/60 text-sm font-medium">
            ← Back
          </Link>
        )}
        {step && totalSteps && (
          <div className="mt-2 mb-3 flex gap-1.5" aria-label={`Step ${step} of ${totalSteps}`}>
            {Array.from({ length: totalSteps }).map((_, i) => (
              <div
                key={i}
                className={`h-1.5 flex-1 rounded-full ${
                  i < step ? "bg-moo-accent" : "bg-moo-ink/10"
                }`}
              />
            ))}
          </div>
        )}
        <h1 className="text-3xl font-semibold leading-tight">{title}</h1>
        {subtitle && <p className="mt-2 text-moo-ink/70 text-base">{subtitle}</p>}
      </header>
      <section className="flex-1 flex flex-col gap-4">{children}</section>
    </main>
  );
}
