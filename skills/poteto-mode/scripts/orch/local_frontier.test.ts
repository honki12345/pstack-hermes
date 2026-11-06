import { afterEach, describe, expect, it } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { UserError, localFrontier } from "./store.ts";

const directories: string[] = [];
const TIMEOUT = 30000;

/** Run git in a throwaway repo and return trimmed stdout. */
function git(repo: string, ...args: string[]): string {
  const result = Bun.spawnSync(["git", "-C", repo, ...args]);
  if (result.exitCode !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${result.stderr.toString()}`);
  }
  return result.stdout.toString().trim();
}

/** Create an isolated repo whose initial branch is `main`. */
async function makeRepo(): Promise<string> {
  const repo = await mkdtemp(join(tmpdir(), "orch-local-"));
  directories.push(repo);
  git(repo, "init", "-q", "--initial-branch=main");
  git(repo, "config", "user.email", "test@example.com");
  git(repo, "config", "user.name", "orch test");
  git(repo, "config", "commit.gpgsign", "false");
  git(repo, "config", "tag.gpgSign", "false");
  return repo;
}

/** Commit an empty change so tests never depend on worktree bytes. */
function commit(repo: string, message: string): void {
  git(repo, "commit", "-q", "--allow-empty", "-m", message);
}

/** Build main -> bottom -> middle -> top, one commit per branch. */
async function linearStack(): Promise<string> {
  const repo = await makeRepo();
  commit(repo, "base");
  git(repo, "checkout", "-q", "-b", "bottom");
  commit(repo, "bottom");
  git(repo, "checkout", "-q", "-b", "middle");
  commit(repo, "middle");
  git(repo, "checkout", "-q", "-b", "top");
  commit(repo, "top");
  return repo;
}

/** Return the branch column of a local frontier. */
function branchNames(repo: string): string[] {
  return localFrontier(repo).map((row) => row.branches);
}

afterEach(async () => {
  await Promise.all(
    directories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true })
    )
  );
});

describe("localFrontier", () => {
  it(
    "orders ancestors, the current branch, then descendants",
    async () => {
      const repo = await linearStack();
      git(repo, "checkout", "-q", "middle");
      expect(branchNames(repo)).toEqual(["main", "bottom", "middle", "top"]);
    },
    TIMEOUT
  );

  it(
    "keeps the same order when the current branch is at the bottom",
    async () => {
      const repo = await linearStack();
      git(repo, "checkout", "-q", "bottom");
      expect(branchNames(repo)).toEqual(["main", "bottom", "middle", "top"]);
    },
    TIMEOUT
  );

  it(
    "breaks equal-depth sibling ties by branch name",
    async () => {
      const repo = await makeRepo();
      commit(repo, "base");
      git(repo, "checkout", "-q", "-b", "zeta");
      commit(repo, "zeta");
      git(repo, "checkout", "-q", "-b", "alpha", "main");
      commit(repo, "alpha");
      git(repo, "checkout", "-q", "main");
      expect(branchNames(repo)).toEqual(["main", "alpha", "zeta"]);
    },
    TIMEOUT
  );

  it(
    "excludes unrelated branches and marks every row with the pr:0 sentinel",
    async () => {
      const repo = await linearStack();
      git(repo, "checkout", "-q", "--orphan", "unrelated");
      commit(repo, "orphan");
      git(repo, "checkout", "-q", "middle");
      const rows = localFrontier(repo);
      expect(rows.map((row) => row.branches)).toEqual([
        "main",
        "bottom",
        "middle",
        "top",
      ]);
      expect(rows.every((row) => row.pr === 0 && row.state === "OPEN")).toBe(
        true
      );
    },
    TIMEOUT
  );

  it(
    "fails when the repository has no branches",
    async () => {
      const repo = await makeRepo();
      expect(() => localFrontier(repo)).toThrow(UserError);
    },
    TIMEOUT
  );
});
