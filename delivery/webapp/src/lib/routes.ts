export function isProjectionRoute(pathname: string | null | undefined) {
  return (
    /^\/songsets\/[^/]+\/play\/projection$/.test(pathname ?? "") ||
    /^\/share\/[^/]+\/play\/projection$/.test(pathname ?? "")
  );
}
