import type { AnchorHTMLAttributes } from "react";
import { isSafeHref } from "./urls";

export function ExternalLink({
  href,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  if (!href || !isSafeHref(href)) {
    return <span {...rest}>{children}</span>;
  }
  const external = /^https?:\/\//i.test(href);
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      {...rest}
    >
      {children}
    </a>
  );
}
