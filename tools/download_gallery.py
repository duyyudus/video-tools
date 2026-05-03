#!/usr/bin/env python3
"""Download images from a multipage WordPress gallery post."""

from __future__ import annotations

import argparse
import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
OUTPUT_EXTENSION = ".jpg"
DEFAULT_WORKERS = 8
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(frozen=True)
class GalleryPage:
    image_urls: list[str]
    page_urls: list[str]
    title: str = ""


@dataclass(frozen=True)
class ParsingRule:
    name: str
    domains: tuple[str, ...]
    image_container_classes: tuple[str, ...] = ()
    image_container_ids: tuple[str, ...] = ()
    stop_image_container_classes: tuple[str, ...] = ()
    pagination_container_classes: tuple[str, ...] = ()
    pagination_link_classes: tuple[str, ...] = ()
    pagination_sequence_regex: str | None = None
    prefer_og_title: bool = False


@dataclass(frozen=True)
class DownloadResult:
    pages: int
    images: int
    saved: int
    skipped: int
    failed: int = 0
    output_folder: Path | None = None


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download article gallery images as 0001.jpg, 0002.jpg, ...",
    )
    parser.add_argument("page_url", help="URL of the first gallery page.")
    parser.add_argument(
        "output_folder",
        type=Path,
        help="Directory where downloaded images should be saved.",
    )
    parser.add_argument(
        "--workers",
        "-j",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel image downloads to run at once (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--no-title-folder",
        action="store_true",
        help="Save directly into OUTPUT_FOLDER instead of a title-named subfolder.",
    )
    return parser.parse_args(argv)


def class_names(attrs: list[tuple[str, str | None]]) -> set[str]:
    for name, value in attrs:
        if name.lower() == "class" and value:
            return set(value.split())
    return set()


def attr_value(attrs: list[tuple[str, str | None]], attr_name: str) -> str | None:
    target = attr_name.lower()
    for name, value in attrs:
        if name.lower() == target:
            return value
    return None


PARSING_RULES = (
    ParsingRule(
        name="trendszine.com",
        domains=("trendszine.com", "img.trendszine.com"),
        image_container_classes=("entry-content",),
        stop_image_container_classes=("page-links",),
        pagination_container_classes=("page-links",),
        pagination_link_classes=("post-page-numbers",),
        pagination_sequence_regex=r"/(\d+)/?$",
    ),
    ParsingRule(
        name="xwxse.com",
        domains=("xwxse.com", "www.xwxse.com"),
        image_container_ids=("list_art_common_art_show",),
        pagination_container_classes=("pagination",),
        pagination_link_classes=("page-link",),
        pagination_sequence_regex=r"-(\d+)/?$",
        prefer_og_title=True,
    ),
)

FALLBACK_PARSING_RULE = PARSING_RULES[0]


def select_parsing_rule(page_url: str) -> ParsingRule:
    host = (urlsplit(page_url).hostname or "").lower()
    for rule in PARSING_RULES:
        if any(host == domain or host.endswith(f".{domain}") for domain in rule.domains):
            return rule
    return FALLBACK_PARSING_RULE


class GalleryHTMLParser(HTMLParser):
    def __init__(self, page_url: str, rule: ParsingRule) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.rule = rule
        self.image_urls: list[str] = []
        self._page_links: dict[int, str] = {}
        self._image_container_depth = 0
        self._capture_images = False
        self._page_links_depth = 0
        self._pending_page_href: str | None = None
        self._pending_page_text: list[str] = []
        self._title_depth = 0
        self._title_text: list[str] = []
        self._og_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        classes = class_names(attrs)
        element_id = attr_value(attrs, "id") or ""

        is_void = tag in VOID_TAGS

        if tag == "meta":
            prop = attr_value(attrs, "property") or attr_value(attrs, "name") or ""
            content = attr_value(attrs, "content") or ""
            if prop.lower() == "og:title" and content:
                self._og_title = content
            return

        if tag == "title" and not self._title_depth:
            self._title_depth = 1
            self._title_text = []
            return
        if self._title_depth and not is_void:
            self._title_depth += 1

        if self._image_container_depth and not is_void:
            self._image_container_depth += 1

        starts_image_container = (
            bool(classes.intersection(self.rule.image_container_classes))
            or element_id in self.rule.image_container_ids
        )
        if starts_image_container and not self._image_container_depth:
            self._image_container_depth = 1
            self._capture_images = True

        if self._image_container_depth and classes.intersection(self.rule.stop_image_container_classes):
            self._capture_images = False

        starts_pagination_container = bool(classes.intersection(self.rule.pagination_container_classes))
        if starts_pagination_container and not self._page_links_depth:
            self._page_links_depth = 1
        elif self._page_links_depth and not is_void:
            self._page_links_depth += 1

        if tag == "img" and self._capture_images:
            src = attr_value(attrs, "src")
            if src:
                self.image_urls.append(urljoin(self.page_url, src))
            return

        if (
            tag == "a"
            and self._page_links_depth
            and classes.intersection(self.rule.pagination_link_classes)
        ):
            href = attr_value(attrs, "href")
            if href:
                self._pending_page_href = urljoin(self.page_url, href)
                self._pending_page_text = []

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self._title_text.append(data)
        if self._pending_page_href:
            self._pending_page_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag == "a" and self._pending_page_href:
            text = "".join(self._pending_page_text).strip()
            page_number = self._extract_page_number(text, self._pending_page_href)
            if page_number is not None:
                self._page_links[page_number] = self._pending_page_href
            self._pending_page_href = None
            self._pending_page_text = []

        if self._page_links_depth:
            self._page_links_depth -= 1

        if self._title_depth:
            self._title_depth -= 1

        if self._image_container_depth:
            self._image_container_depth -= 1
            if not self._image_container_depth:
                self._capture_images = False

    @property
    def page_urls(self) -> list[str]:
        pages = {1: self.page_url}
        pages.update(self._page_links)
        if self.rule.pagination_sequence_regex and self._page_links:
            last_page = max(pages)
            sequence_pages = self._build_sequence_pages(last_page, pages)
            if sequence_pages:
                pages.update(sequence_pages)
        return [url for _, url in sorted(pages.items())]

    @property
    def title(self) -> str:
        if self.rule.prefer_og_title and self._og_title:
            return " ".join(self._og_title.split())
        return " ".join("".join(self._title_text).split())

    def _extract_page_number(self, text: str, href: str) -> int | None:
        if re.fullmatch(r"\d+", text):
            return int(text)
        if self.rule.pagination_sequence_regex:
            match = re.search(self.rule.pagination_sequence_regex, urlsplit(href).path)
            if match:
                return int(match.group(1))
        return None

    def _build_sequence_pages(self, last_page: int, known_pages: dict[int, str]) -> dict[int, str]:
        if not self.rule.pagination_sequence_regex:
            return {}

        template_url = self.page_url
        template_match = re.search(self.rule.pagination_sequence_regex, urlsplit(template_url).path)
        if not template_match:
            for page_number, page_url in sorted(known_pages.items()):
                if page_number <= 1:
                    continue
                if re.search(self.rule.pagination_sequence_regex, urlsplit(page_url).path):
                    template_url = page_url
                    break
            template_match = re.search(self.rule.pagination_sequence_regex, urlsplit(template_url).path)

        if not template_match:
            return {}

        current_path = urlsplit(template_url).path
        pages: dict[int, str] = {}
        for page_number in range(1, last_page + 1):
            if page_number == 1 and 1 in known_pages:
                pages[page_number] = known_pages[1]
                continue
            path = (
                current_path[: template_match.start(1)]
                + str(page_number)
                + current_path[template_match.end(1) :]
            )
            parts = urlsplit(template_url)
            pages[page_number] = urlunsplit(
                (parts.scheme, parts.netloc, path, parts.query, parts.fragment)
            )
        return pages


def parse_gallery_page(html: str, page_url: str) -> GalleryPage:
    parser = GalleryHTMLParser(page_url, select_parsing_rule(page_url))
    parser.feed(html)
    parser.close()
    return GalleryPage(
        image_urls=parser.image_urls,
        page_urls=parser.page_urls,
        title=parser.title,
    )


def sanitize_folder_name(title: str) -> str:
    decoded = html.unescape(title)
    normalized = " ".join(decoded.split()).strip()
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized)
    sanitized = sanitized.rstrip(" .")
    return sanitized or "gallery"


def quote_request_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&?/:;+,%@")
    fragment = quote(parts.fragment, safe="=&?/:;+,%@")

    netloc = parts.netloc
    if parts.hostname:
        host = parts.hostname.encode("idna").decode("ascii")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        userinfo = ""
        if parts.username:
            userinfo = quote(parts.username, safe="")
            if parts.password:
                userinfo += f":{quote(parts.password, safe='')}"
            userinfo += "@"
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{userinfo}{host}{port}"

    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def fetch_url_text(url: str) -> str:
    request_url = quote_request_url(url)
    request = Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def fetch_url_binary(url: str, referer: str) -> bytes:
    request_url = quote_request_url(url)
    request_referer = quote_request_url(referer)
    request = Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": request_referer,
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return response.read()


def output_path_for(output_dir: Path, image_number: int) -> Path:
    return output_dir / f"{image_number:04d}{OUTPUT_EXTENSION}"


def has_complete_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def write_image(path: Path, content: bytes) -> None:
    if not content:
        raise ValueError(f"Downloaded image for {path.name} was empty.")
    tmp_path = path.with_name(f"{path.name}.part")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


def download_gallery(
    page_url: str,
    output_folder: Path,
    *,
    fetch_text: Callable[[str], str] = fetch_url_text,
    fetch_binary: Callable[[str, str], bytes] = fetch_url_binary,
    max_workers: int = DEFAULT_WORKERS,
    use_title_folder: bool = True,
) -> DownloadResult:
    if max_workers <= 0:
        raise ValueError("Workers must be a positive integer.")

    base_output_dir = output_folder.expanduser().resolve()

    first_html = fetch_text(page_url)
    first_page = parse_gallery_page(first_html, page_url)
    output_dir = base_output_dir
    if use_title_folder and first_page.title:
        output_dir = base_output_dir / sanitize_folder_name(first_page.title)
    output_dir.mkdir(parents=True, exist_ok=True)

    page_urls = first_page.page_urls or [page_url]
    page_html_by_url = {page_url: first_html}

    seen_urls: set[str] = set()
    image_refs: list[tuple[str, str]] = []

    for index, current_url in enumerate(page_urls, start=1):
        html = page_html_by_url.get(current_url)
        if html is None:
            html = fetch_text(current_url)
        page = first_page if current_url == page_url else parse_gallery_page(html, current_url)
        print(f"Fetched page {index}/{len(page_urls)}: {current_url} ({len(page.image_urls)} images)")
        for image_url in page.image_urls:
            if image_url in seen_urls:
                continue
            seen_urls.add(image_url)
            image_refs.append((image_url, current_url))

    if not image_refs:
        raise ValueError(f"No gallery images found in {page_url}.")

    print(f"Total images to download: {len(image_refs)}")

    saved = 0
    skipped = 0
    failed = 0
    pending: list[tuple[int, str, str, Path]] = []
    for image_number, (image_url, referer) in enumerate(image_refs, start=1):
        path = output_path_for(output_dir, image_number)
        if has_complete_file(path):
            skipped += 1
            print(f"Skipped {path.name}")
            continue
        pending.append((image_number, image_url, referer, path))

    def download_one(item: tuple[int, str, str, Path]) -> Path:
        _, image_url, referer, path = item
        content = fetch_binary(image_url, referer)
        write_image(path, content)
        return path

    workers = min(max_workers, len(pending)) if pending else 0
    if workers:
        print(f"Downloading {len(pending)} images with {workers} workers...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(download_one, item): item
                for item in pending
            }
            for future in as_completed(futures):
                _, image_url, _, path = futures[future]
                try:
                    saved_path = future.result()
                except Exception as exc:
                    failed += 1
                    print(f"Failed {path.name}: {image_url} ({exc})", file=sys.stderr)
                    continue
                saved += 1
                print(f"Saved {saved_path.name}")

    print(
        f"Complete: {len(page_urls)} pages, {len(image_refs)} images, "
        f"{saved} saved, {skipped} skipped, {failed} failed."
    )
    return DownloadResult(
        pages=len(page_urls),
        images=len(image_refs),
        saved=saved,
        skipped=skipped,
        failed=failed,
        output_folder=output_dir,
    )


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    download_gallery(
        args.page_url,
        args.output_folder,
        max_workers=args.workers,
        use_title_folder=not args.no_title_folder,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
