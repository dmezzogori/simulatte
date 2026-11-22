import { isKnownLayoutVersion } from "@simulatte/trace";

/** How the info line names the geometry source: a known recorded layout, or the viewer's own fallback. */
export const layoutLabel = (layout: { readonly schema_version: string } | null | undefined): string =>
  isKnownLayoutVersion(layout?.schema_version) ? "recorded layout" : "viewer fallback layout";
