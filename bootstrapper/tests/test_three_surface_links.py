from scripts.docs.links import find_links, is_forbidden


def test_find_links_returns_links_and_images_without_code_fences() -> None:
    markdown = """
[guide](docs/guide.md)
![diagram](assets/diagram.svg)
```
[ignored](https://thekaveh.github.io/atlas/)
```
"""

    links = find_links(markdown)

    assert [(link.target, link.is_image) for link in links] == [
        ("docs/guide.md", False),
        ("assets/diagram.svg", True),
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
