import Link from "next/link";

type ButtonProps =
  | (React.ButtonHTMLAttributes<HTMLButtonElement> & {
      href?: undefined;
      variant?: "primary" | "secondary";
    })
  | (React.AnchorHTMLAttributes<HTMLAnchorElement> & {
      href: string;
      variant?: "primary" | "secondary";
    });

export function Button(props: ButtonProps) {
  const variant = props.variant ?? "primary";
  const className = `${variant === "primary" ? "moo-button" : "moo-button-secondary"} ${
    props.className ?? ""
  }`;
  if ("href" in props && props.href) {
    const { variant: _v, className: _c, href, ...rest } = props;
    return (
      <Link href={href} className={className} {...rest}>
        {props.children}
      </Link>
    );
  }
  const { variant: _v, className: _c, ...rest } = props as React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string };
  return <button className={className} {...rest} />;
}
