/**
 * Build a forge-free frontier from local branches: the current branch, its
 * ancestors, and its descendants, ordered bottom-to-top with PR sentinel 0.
 */
export function localFrontier(repo: string): readonly FrontierPr[] {
  let currentBranch: string;
  try {
    currentBranch = execFileSync(
      "git",
      ["rev-parse", "--abbrev-ref", "HEAD"],
      { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] }
    ).trim();
  } catch (error) {
    throw new UserError(`git rev-parse HEAD failed: ${errorMessage(error)}`);
  }

  let allBranches: string[];
  try {
    allBranches = execFileSync(
      "git",
      ["branch", "--format=%(refname:short)"],
      { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] }
    )
      .split("\n")
      .map((b) => b.trim())
      .filter((b) => b.length > 0);
  } catch (error) {
    throw new UserError(`git branch listing failed: ${errorMessage(error)}`);
  }

  const stackBranches: string[] = [];
  for (const branch of allBranches) {
    if (branch === currentBranch) { stackBranches.push(branch); continue; }
    try {
      execFileSync("git", ["merge-base", "--is-ancestor", `refs/heads/${branch}`, "HEAD"], { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] });
      stackBranches.push(branch);
    } catch {
      try {
        execFileSync("git", ["merge-base", "--is-ancestor", "HEAD", `refs/heads/${branch}`], { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] });
        stackBranches.push(branch);
      } catch { /* unrelated */ }
    }
  }

  // Order by ancestry without a pairwise comparator: rev-list counts grow
  // monotonically from ancestor to descendant, so sorting by count puts the
  // bottom of the stack first. Siblings and equal SHAs tiebreak by code unit,
  // which keeps the comparator total and locale-independent.
  const depth = new Map<string, number>();
  for (const branch of stackBranches) {
    let count: number;
    try {
      count = Number(
        execFileSync(
          "git",
          ["rev-list", "--count", `refs/heads/${branch}`],
          { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] }
        ).trim()
      );
    } catch (error) {
      throw new UserError(`git rev-list --count ${branch} failed: ${errorMessage(error)}`);
    }
    if (!Number.isSafeInteger(count) || count < 1) {
      throw new UserError(`git rev-list --count ${branch} returned an invalid count: ${count}`);
    }
    depth.set(branch, count);
  }
  stackBranches.sort((a, b) => {
    const da = depth.get(a) ?? Number.MAX_SAFE_INTEGER;
    const db = depth.get(b) ?? Number.MAX_SAFE_INTEGER;
    if (da !== db) return da - db;
    return a < b ? -1 : a > b ? 1 : 0;
  });

  const result: FrontierPr[] = [];
  for (const branch of stackBranches) {
    try {
      const sha = execFileSync("git", ["rev-parse", `refs/heads/${branch}`], { cwd: repo, encoding: "utf8", env: { ...process.env, NO_COLOR: "1" }, stdio: ["ignore", "pipe", "pipe"] }).trim();
      result.push({ pr: 0, branches: branch, sha, state: "OPEN" });
    } catch (error) { /* skip */ }
  }
  if (result.length === 0) {
    throw new UserError("local stack discovery found no branches in the current stack");
  }
  return result;
}
