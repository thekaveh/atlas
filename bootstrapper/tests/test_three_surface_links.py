from scripts.docs.links import find_links, is_forbidden, navigable_link_targets


def test_find_links_returns_links_and_images_without_code_fences() -> None:
    markdown = """
[guide](docs/guide.md)
![diagram](assets/diagram.svg)
<a href="docs/html-guide.md">HTML guide</a>
<img src="assets/html-diagram.svg" alt="HTML diagram">
```
[ignored](https://thekaveh.github.io/atlas/)
<a href="https://github.com/thekaveh/atlas/wiki/Ignored">ignored</a>
```
"""

    links = find_links(markdown)

    assert [(link.target, link.is_image) for link in links] == [
        ("docs/guide.md", False),
        ("assets/diagram.svg", True),
        ("docs/html-guide.md", False),
        ("assets/html-diagram.svg", True),
    ]


def test_forbidden_link_matrix_is_surface_specific() -> None:
    repo = "https://github.com/thekaveh/atlas/blob/main/docs/index.md"
    site = "https://thekaveh.github.io/atlas/"
    wiki = "https://github.com/thekaveh/atlas/wiki/Overview"

    assert is_forbidden(site, "repo")
    assert is_forbidden(wiki, "repo")
    assert is_forbidden(repo, "site")
    assert is_forbidden(wiki, "site")
    assert is_forbidden(repo, "wiki")
    assert is_forbidden(site, "wiki")
    assert not is_forbidden("https://docs.docker.com/", "site")
    assert not is_forbidden("guide.md", "wiki")


def test_navigable_links_include_commonmark_autolinks_and_raw_html_anchors() -> None:
    markdown = """
<https://example.com/guide>
<a href="guide.md?one=1&amp;two=2">Guide</a>
![image](ignored.md)
<!-- <a href="comment.md">Comment</a> -->
<script><a href="script.md">Script</a></script>
"""

    assert navigable_link_targets(markdown) == [
        "https://example.com/guide",
        "guide.md?one=1&two=2",
    ]
