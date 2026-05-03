from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import download_gallery


FIRST_PAGE = """
<html><body>
<article>
  <div class="entry-content">
    <p>
      <img class="aligncenter" src="https://img.example.test/one.jpg" alt="1">
      <img class="aligncenter" src="/two.jpg" alt="2">
    </p>
    <div class="page-links">
      Pages:
      <span class="post-page-numbers current">1</span>
      <a href="https://example.test/post.html/3" class="post-page-numbers">3</a>
      <a href="https://example.test/post.html/2" class="post-page-numbers">2</a>
    </div>
  </div>
</article>
</body></html>
"""


SECOND_PAGE = """
<html><body>
<article>
  <div class="entry-content">
    <p>
      <img class="aligncenter" src="https://img.example.test/three.jpg" alt="3">
    </p>
    <div class="page-links">
      <a href="https://example.test/post.html" class="post-page-numbers">1</a>
      <span class="post-page-numbers current">2</span>
      <a href="https://example.test/post.html/3" class="post-page-numbers">3</a>
    </div>
  </div>
</article>
</body></html>
"""


THIRD_PAGE = """
<html><body>
<article>
  <div class="entry-content">
    <p>
      <img class="aligncenter" src="https://img.example.test/four.jpg" alt="4">
      <img class="aligncenter" src="https://img.example.test/four.jpg" alt="duplicate">
    </p>
    <div class="page-links">
      <a href="https://example.test/post.html" class="post-page-numbers">1</a>
      <a href="https://example.test/post.html/2" class="post-page-numbers">2</a>
      <span class="post-page-numbers current">3</span>
    </div>
  </div>
</article>
</body></html>
"""


XWXSE_PAGE = """
<html>
  <head>
    <title>Verbose browser title - xwxse.com</title>
    <meta property="og:title" content="Xwxse Gallery Title" />
  </head>
  <body>
    <img src="/assets/images/logo.png">
    <section class="content-header">
      <h2>Xwxse Gallery Title</h2>
    </section>
    <div id="list_art_common_art_show">
      <img src="https://assets.xwxse.com/Uploads/newsallpic/2024-12-17/one.webp">
      <img src="https://assets.xwxse.com/Uploads/newsallpic/2024-12-17/two.webp">
    </div>
    <ul class="pagination">
      <li><span class="page-link active disabled">1</span></li>
      <li><a class="page-link" href="/artdetail/gallery-slug-2/">2</a></li>
      <li><a class="page-link" href="/artdetail/gallery-slug-3/">3</a></li>
      <li><a class="page-link" href="/artdetail/gallery-slug-5/">最後 &raquo;</a></li>
    </ul>
    <div class="related">
      <img data-src="https://assets.xwxse.com/Uploads-s/news/related.webp">
    </div>
  </body>
</html>
"""


def test_extracts_article_images_and_numeric_pagination_in_order() -> None:
    html = FIRST_PAGE.replace(
        "<html><body>",
        "<html><head><title>Gallery Title - Site</title></head><body>",
    )
    parsed = download_gallery.parse_gallery_page(html, "https://example.test/post.html")

    assert parsed.title == "Gallery Title - Site"
    assert parsed.image_urls == [
        "https://img.example.test/one.jpg",
        "https://example.test/two.jpg",
    ]
    assert parsed.page_urls == [
        "https://example.test/post.html",
        "https://example.test/post.html/2",
        "https://example.test/post.html/3",
    ]


def test_trendszine_rule_expands_sparse_page_links() -> None:
    html = """
    <div class="entry-content">
      <img src="https://img.example.test/article.jpg">
      <div class="page-links">
        <span class="post-page-numbers current">1</span>
        <a class="post-page-numbers" href="https://trendszine.com/post.html/2">2</a>
        <a class="post-page-numbers" href="https://trendszine.com/post.html/3">3</a>
        <a class="post-page-numbers" href="https://trendszine.com/post.html/10">10</a>
      </div>
    </div>
    """

    parsed = download_gallery.parse_gallery_page(html, "https://trendszine.com/post.html")

    assert parsed.page_urls == [
        "https://trendszine.com/post.html",
        "https://trendszine.com/post.html/2",
        "https://trendszine.com/post.html/3",
        "https://trendszine.com/post.html/4",
        "https://trendszine.com/post.html/5",
        "https://trendszine.com/post.html/6",
        "https://trendszine.com/post.html/7",
        "https://trendszine.com/post.html/8",
        "https://trendszine.com/post.html/9",
        "https://trendszine.com/post.html/10",
    ]


def test_selects_named_parsing_rules_by_domain() -> None:
    assert download_gallery.select_parsing_rule("https://trendszine.com/post.html").name == "trendszine.com"
    assert download_gallery.select_parsing_rule("https://img.trendszine.com/post.html").name == "trendszine.com"
    assert download_gallery.select_parsing_rule("https://xwxse.com/artdetail/foo-1/").name == "xwxse.com"


def test_xwxse_rule_extracts_main_images_title_and_full_page_sequence() -> None:
    parsed = download_gallery.parse_gallery_page(
        XWXSE_PAGE,
        "https://xwxse.com/artdetail/gallery-slug-1/",
    )

    assert parsed.title == "Xwxse Gallery Title"
    assert parsed.image_urls == [
        "https://assets.xwxse.com/Uploads/newsallpic/2024-12-17/one.webp",
        "https://assets.xwxse.com/Uploads/newsallpic/2024-12-17/two.webp",
    ]
    assert parsed.page_urls == [
        "https://xwxse.com/artdetail/gallery-slug-1/",
        "https://xwxse.com/artdetail/gallery-slug-2/",
        "https://xwxse.com/artdetail/gallery-slug-3/",
        "https://xwxse.com/artdetail/gallery-slug-4/",
        "https://xwxse.com/artdetail/gallery-slug-5/",
    ]


def test_xwxse_rule_expands_sequence_when_first_page_has_no_suffix() -> None:
    parsed = download_gallery.parse_gallery_page(
        XWXSE_PAGE.replace("gallery-slug-5/", "gallery-slug-12/"),
        "https://xwxse.com/artdetail/gallery-slug/",
    )

    assert parsed.page_urls == [
        "https://xwxse.com/artdetail/gallery-slug/",
        "https://xwxse.com/artdetail/gallery-slug-2/",
        "https://xwxse.com/artdetail/gallery-slug-3/",
        "https://xwxse.com/artdetail/gallery-slug-4/",
        "https://xwxse.com/artdetail/gallery-slug-5/",
        "https://xwxse.com/artdetail/gallery-slug-6/",
        "https://xwxse.com/artdetail/gallery-slug-7/",
        "https://xwxse.com/artdetail/gallery-slug-8/",
        "https://xwxse.com/artdetail/gallery-slug-9/",
        "https://xwxse.com/artdetail/gallery-slug-10/",
        "https://xwxse.com/artdetail/gallery-slug-11/",
        "https://xwxse.com/artdetail/gallery-slug-12/",
    ]


def test_sanitize_folder_name_removes_path_unsafe_characters() -> None:
    assert (
        download_gallery.sanitize_folder_name('A/B: "Gallery" * Test? <01> |')
        == "A_B_ _Gallery_ _ Test_ _01_ _"
    )


def test_uses_title_subfolder_by_default(tmp_path: Path) -> None:
    def fetch_text(url: str) -> str:
        return """
        <html>
          <head><title>My Gallery / Page: 01</title></head>
          <body>
            <div class="entry-content">
              <img src="https://img.example.test/one.jpg">
            </div>
          </body>
        </html>
        """

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=lambda url, referer: b"one",
        max_workers=1,
    )

    assert result.output_folder == tmp_path.resolve() / "My Gallery _ Page_ 01"
    assert (tmp_path / "My Gallery _ Page_ 01" / "0001.jpg").read_bytes() == b"one"
    assert not (tmp_path / "0001.jpg").exists()


def test_title_subfolder_can_be_disabled(tmp_path: Path) -> None:
    def fetch_text(url: str) -> str:
        return """
        <html>
          <head><title>My Gallery</title></head>
          <body>
            <div class="entry-content">
              <img src="https://img.example.test/one.jpg">
            </div>
          </body>
        </html>
        """

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=lambda url, referer: b"one",
        max_workers=1,
        use_title_folder=False,
    )

    assert result.output_folder == tmp_path.resolve()
    assert (tmp_path / "0001.jpg").read_bytes() == b"one"


def test_extracts_only_entry_content_before_page_links() -> None:
    html = """
    <div class="entry-content">
      <img src="https://img.example.test/article.jpg">
      <div class="page-links"><a class="post-page-numbers" href="/post/2">2</a></div>
      <img src="https://img.example.test/related.jpg">
    </div>
    <aside><img src="https://img.example.test/sidebar.jpg"></aside>
    """

    parsed = download_gallery.parse_gallery_page(html, "https://example.test/post.html")

    assert parsed.image_urls == ["https://img.example.test/article.jpg"]


def test_entry_content_scope_ends_after_container_without_pagination() -> None:
    html = """
    <div class="entry-content">
      <p><img src="https://img.example.test/article.jpg"></p>
    </div>
    <aside><img src="https://img.example.test/sidebar.jpg"></aside>
    """

    parsed = download_gallery.parse_gallery_page(html, "https://example.test/post.html")

    assert parsed.image_urls == ["https://img.example.test/article.jpg"]


def test_downloads_multipage_gallery_with_global_numbering(tmp_path: Path) -> None:
    html_by_url = {
        "https://example.test/post.html": FIRST_PAGE,
        "https://example.test/post.html/2": SECOND_PAGE,
        "https://example.test/post.html/3": THIRD_PAGE,
    }
    images_by_url = {
        "https://img.example.test/one.jpg": b"one",
        "https://example.test/two.jpg": b"two",
        "https://img.example.test/three.jpg": b"three",
        "https://img.example.test/four.jpg": b"four",
    }

    def fetch_text(url: str) -> str:
        return html_by_url[url]

    def fetch_binary(url: str, referer: str) -> bytes:
        assert referer.startswith("https://example.test/post.html")
        return images_by_url[url]

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=fetch_binary,
    )

    assert result.saved == 4
    assert result.skipped == 0
    assert result.pages == 3
    assert (tmp_path / "0001.jpg").read_bytes() == b"one"
    assert (tmp_path / "0002.jpg").read_bytes() == b"two"
    assert (tmp_path / "0003.jpg").read_bytes() == b"three"
    assert (tmp_path / "0004.jpg").read_bytes() == b"four"


def test_prints_total_images_after_page_fetches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def fetch_text(url: str) -> str:
        return """
        <div class="entry-content">
          <img src="https://img.example.test/one.jpg">
          <img src="https://img.example.test/two.jpg">
        </div>
        """

    download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=lambda url, referer: b"image",
        max_workers=1,
    )

    output = capsys.readouterr().out.splitlines()

    assert output[0].startswith("Fetched page 1/1:")
    assert output[1] == "Total images to download: 2"
    assert output[2] == "Downloading 2 images with 1 workers..."


def test_resume_skips_nonempty_files_and_redownloads_empty_files(tmp_path: Path) -> None:
    (tmp_path / "0001.jpg").write_bytes(b"existing")
    (tmp_path / "0002.jpg").write_bytes(b"")
    calls: list[str] = []

    def fetch_text(url: str) -> str:
        return """
        <div class="entry-content">
          <img src="https://img.example.test/one.jpg">
          <img src="https://img.example.test/two.jpg">
        </div>
        """

    def fetch_binary(url: str, referer: str) -> bytes:
        calls.append(url)
        return b"new"

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=fetch_binary,
    )

    assert result.saved == 1
    assert result.skipped == 1
    assert calls == ["https://img.example.test/two.jpg"]
    assert (tmp_path / "0001.jpg").read_bytes() == b"existing"
    assert (tmp_path / "0002.jpg").read_bytes() == b"new"


def test_downloads_images_in_parallel_while_preserving_numbering(tmp_path: Path) -> None:
    html = """
    <div class="entry-content">
      <img src="https://img.example.test/one.jpg">
      <img src="https://img.example.test/two.jpg">
      <img src="https://img.example.test/three.jpg">
    </div>
    """
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fetch_text(url: str) -> str:
        return html

    def fetch_binary(url: str, referer: str) -> bytes:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return url.rsplit("/", 1)[-1].encode("ascii")

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=fetch_binary,
        max_workers=3,
    )

    assert result.saved == 3
    assert max_active > 1
    assert (tmp_path / "0001.jpg").read_bytes() == b"one.jpg"
    assert (tmp_path / "0002.jpg").read_bytes() == b"two.jpg"
    assert (tmp_path / "0003.jpg").read_bytes() == b"three.jpg"


def test_quote_request_url_percent_encodes_unicode_path_and_query() -> None:
    url = download_gallery.quote_request_url(
        "https://example.test/相册/頁 1.jpg?title=測試&ok=1"
    )

    assert url == (
        "https://example.test/%E7%9B%B8%E5%86%8C/"
        "%E9%A0%81%201.jpg?title=%E6%B8%AC%E8%A9%A6&ok=1"
    )


def test_failed_image_download_does_not_stop_remaining_images(tmp_path: Path) -> None:
    html = """
    <div class="entry-content">
      <img src="https://img.example.test/one.jpg">
      <img src="https://img.example.test/fail.jpg">
      <img src="https://img.example.test/three.jpg">
    </div>
    """

    def fetch_text(url: str) -> str:
        return html

    def fetch_binary(url: str, referer: str) -> bytes:
        if url.endswith("/fail.jpg"):
            raise UnicodeEncodeError("ascii", "bad 測試", 4, 6, "ordinal not in range")
        return url.rsplit("/", 1)[-1].encode("ascii")

    result = download_gallery.download_gallery(
        "https://example.test/post.html",
        tmp_path,
        fetch_text=fetch_text,
        fetch_binary=fetch_binary,
        max_workers=3,
    )

    assert result.saved == 2
    assert result.failed == 1
    assert (tmp_path / "0001.jpg").read_bytes() == b"one.jpg"
    assert not (tmp_path / "0002.jpg").exists()
    assert (tmp_path / "0003.jpg").read_bytes() == b"three.jpg"


def test_download_gallery_raises_when_no_article_images(tmp_path: Path) -> None:
    def fetch_text(url: str) -> str:
        return '<div class="entry-content"><p>No images here.</p></div>'

    with pytest.raises(ValueError, match="No gallery images found"):
        download_gallery.download_gallery(
            "https://example.test/post.html",
            tmp_path,
            fetch_text=fetch_text,
            fetch_binary=lambda url, referer: b"",
        )
