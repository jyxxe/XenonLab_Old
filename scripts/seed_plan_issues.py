"""Create the plan's requested first issues without declaring incomplete work done."""

import json
import re
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
users = json.loads((root / "forgejo-users.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
repository = "/repos/xenon-admin/XeonoLab"
text = Path("plan.md").read_text().split("# 100. 推荐首批 Issues", 1)[1].split("# 101.", 1)[0]
tasks = re.findall(r"^#(\d+)\s+(.+)$", text, re.M)
with httpx.Client(
    base_url="https://192.168.0.101:9445/api/v1",
    verify=context,
    trust_env=False,
    timeout=60,
    auth=("xenon-admin", users["xenon-admin"]),
) as client:
    titles = set()
    for page in range(1, 100):
        result = client.get(repository + "/issues", params={"state": "all", "limit": 50, "page": page})
        result.raise_for_status()
        data = result.json()
        titles.update(item["title"] for item in data)
        if len(data) < 50:
            break
    for number, title in tasks:
        title = f"[Plan {number}] {title}"
        if title in titles:
            continue
        client.post(
            repository + "/issues",
            json={
                "title": title,
                "body": f"来源：plan.md §100 推荐首批任务 #{number}。\n\n"
                "当前状态需根据源码与线上行为审计；本 Issue 保持打开，不能从已有 MVP 推断完成。\n\n"
                "领取方式：指派独立 Harness 账户并留言说明范围；实现使用分支和 PR。\n\n"
                "验收：实现、单元/集成/权限测试、错误处理、日志与关键 metrics、OpenAPI、文档、"
                "生产依赖说明、不泄露凭据、不绕过 Deploy Gateway。提供实际线上验证证据后关闭。",
            },
        ).raise_for_status()
    client.patch(repository, json={"default_branch": "feature/platform-v1"}).raise_for_status()
    result = client.get(repository + "/branch_protections/main")
    if result.status_code == 404:
        client.post(
            repository + "/branch_protections",
            json={
                "branch_name": "main",
                "enable_push": False,
                "enable_force_push": False,
                "required_approvals": 1,
            },
        ).raise_for_status()
    else:
        result.raise_for_status()
print(f"Seeded {len(tasks)} plan tasks; implementation branch visible; main direct pushes protected")
