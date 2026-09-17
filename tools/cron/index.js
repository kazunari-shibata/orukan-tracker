// .github/workflows/update.yml の workflow_dispatch を叩く。
// GH_TOKEN は wrangler secret put GH_TOKEN で入れる（このリポジトリだけに絞った
// fine-grained PAT、権限は Actions: write のみ）。
const WORKFLOW =
  "https://api.github.com/repos/kazunari-shibata/orukan-tracker" +
  "/actions/workflows/update.yml/dispatches";

export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(WORKFLOW, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        // GitHub API は User-Agent が無いと 403 を返す。
        "User-Agent": "orukan-cron",
      },
      body: JSON.stringify({ ref: "main" }),
    });

    // 成功は 204 No Content。失敗を投げておくと Workers のログと
    // ダッシュボードのエラー率に出るので、PAT の期限切れに気づける。
    if (!res.ok) {
      throw new Error(`workflow_dispatch failed: ${res.status} ${await res.text()}`);
    }
  },
};
