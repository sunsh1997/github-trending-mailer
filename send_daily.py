from __future__ import annotations

import datetime as dt
import email.utils
import html
import os
import re
import smtplib
import ssl
import textwrap
import urllib.error
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo


TRENDING_URL = "https://github.com/trending?since=daily"
TIMEZONE = ZoneInfo("Asia/Shanghai")


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_text(url: str, token: str | None = None) -> str:
    headers = {
        "Accept": "text/html,application/json",
        "User-Agent": "github-trending-mailer/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_count(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def parse_github_trending(page: str) -> list[dict[str, object]]:
    articles = re.findall(r"<article\b[\s\S]*?</article>", page)
    repos: list[dict[str, object]] = []

    for article in articles:
        link_match = re.search(r'<h2[\s\S]*?<a[^>]+href="/([^"/]+/[^"/]+)"[^>]*>', article)
        if not link_match:
            continue

        full_name = html.unescape(link_match.group(1)).strip()
        description_match = re.search(
            r'<p[^>]+class="[^"]*col-9[^"]*"[^>]*>([\s\S]*?)</p>', article
        )
        language_match = re.search(
            r'<span[^>]+itemprop="programmingLanguage"[^>]*>([\s\S]*?)</span>', article
        )
        stars_today_match = re.search(r"(\d[\d,]*)\s+stars?\s+today", article, re.I)

        repos.append(
            {
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": strip_tags(description_match.group(1)) if description_match else "",
                "language": strip_tags(language_match.group(1)) if language_match else "",
                "stars_today": parse_count(stars_today_match.group(1) if stars_today_match else None),
                "stars": None,
                "forks": None,
                "topics": [],
            }
        )

    return repos


def fetch_repo_metadata(full_name: str, token: str | None) -> dict[str, object]:
    url = f"https://api.github.com/repos/{full_name}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-trending-mailer/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        import json

        return json.loads(response.read().decode("utf-8"))


def enrich_repos(repos: list[dict[str, object]], token: str | None) -> None:
    for repo in repos:
        try:
            meta = fetch_repo_metadata(str(repo["full_name"]), token)
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue

        repo["stars"] = meta.get("stargazers_count")
        repo["forks"] = meta.get("forks_count")
        repo["description"] = repo["description"] or meta.get("description") or ""
        repo["language"] = repo["language"] or meta.get("language") or ""
        repo["topics"] = meta.get("topics") or []


def format_count(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "未知"


def build_report(repos: list[dict[str, object]], report_date: dt.date) -> str:
    title = f"# GitHub 飙升项目日报 - {report_date.isoformat()}"
    intro = (
        "数据口径：GitHub Trending daily 主要给出过去约 24 小时的 star 增量；"
        "GitHub API 只提供当前总 star/fork 数，不提供 fork 日增量。"
    )

    lines = [title, "", intro, "", "## 今日重点"]

    for index, repo in enumerate(repos, 1):
        language = f" / {repo['language']}" if repo.get("language") else ""
        topics = repo.get("topics") or []
        topic_text = f" / topics: {', '.join(topics[:4])}" if topics else ""
        description = str(repo.get("description") or "暂无简介")

        lines.extend(
            [
                "",
                f"### {index}. [{repo['full_name']}]({repo['url']}){language}{topic_text}",
                f"- Star 增量：+{format_count(repo.get('stars_today'))}",
                f"- 当前总量：{format_count(repo.get('stars'))} stars / {format_count(repo.get('forks'))} forks",
                f"- 项目方向：{description}",
                f"- 为什么值得关注：短时间 star 增长靠前，说明它正在进入开发者讨论和试用视野。",
            ]
        )

    lines.extend(
        [
            "",
            "## 趋势观察",
            "",
            "今天的榜单适合优先看两类项目：一类是能直接嵌入开发流程的工具，另一类是可复用的学习资料、agent skills 或模板库。fork 日增量没有官方稳定口径，因此这里用当前 fork 总量辅助判断社区参与度。",
            "",
            "## 数据来源",
            "",
            f"- GitHub Trending daily: {TRENDING_URL}",
            "- GitHub REST API: https://api.github.com/repos/{owner}/{repo}",
        ]
    )

    return "\n".join(lines) + "\n"


def send_email(subject: str, body: str) -> None:
    smtp_host = env_required("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = env_required("SMTP_USER")
    smtp_pass = env_required("SMTP_PASS")
    mail_from = os.environ.get("MAIL_FROM", smtp_user).strip() or smtp_user
    recipients = [item.strip() for item in env_required("MAIL_TO").split(",") if item.strip()]

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = str(Header(subject, "utf-8"))
    message["From"] = email.utils.formataddr(("GitHub Trending", mail_from))
    message["To"] = ", ".join(recipients)

    if smtp_port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(mail_from, recipients, message.as_string())
    else:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(mail_from, recipients, message.as_string())


def main() -> None:
    now = dt.datetime.now(TIMEZONE)
    report_date = (now - dt.timedelta(days=1)).date()
    token = os.environ.get("GH_TOKEN", "").strip() or None
    max_repos = int(os.environ.get("MAX_REPOS", "12"))

    page = fetch_text(TRENDING_URL)
    repos = parse_github_trending(page)
    repos = [repo for repo in repos if repo.get("stars_today")]
    repos.sort(key=lambda repo: int(repo.get("stars_today") or 0), reverse=True)
    repos = repos[:max_repos]

    if not repos:
        raise RuntimeError("No GitHub Trending repositories were parsed.")

    enrich_repos(repos, token)
    report = build_report(repos, report_date)

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"github-trending-{report_date.isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")

    subject = f"GitHub 飙升项目日报 - {report_date.isoformat()}"
    send_email(subject, report)
    print(textwrap.shorten(f"Sent {len(repos)} repos to {os.environ.get('MAIL_TO', '')}", width=100))


if __name__ == "__main__":
    main()
